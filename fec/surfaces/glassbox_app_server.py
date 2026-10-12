# -*- coding: utf-8 -*-
"""GLASS BOX app server.

A read-only local web application over the shared Glass Box event stream
(one immutable JSON file per event in ``~/.claude/glassbox/events``).

Design notes
------------
* READ-ONLY with respect to the event stream: this process never deletes,
  moves, or rewrites an event file. It only appends its own lifecycle
  events (started / heartbeat / stopped) so that the app itself is visible
  in the stream it observes, per the "everything must emit" rule. It never
  appends ``lane.abandoned`` markers -- that remains the reaper's job.
* INCREMENTAL scanning: a poll is a single directory enumeration. Only
  files whose (mtime_ns, size) fingerprint changed are opened and parsed,
  so the cost of a poll does not grow with the number of historical events.
* A ROW is the unique triple (lane, source, run_id); its state is the newest
  event for that triple. Newest is decided on file mtime, which is exactly
  what ``reap_abandoned_glassbox_tasks.ps1`` uses.
* Abandoned classification mirrors the reaper bit for bit: a live-ish status,
  newest event older than the stale threshold, and the writer PID (encoded in
  the filename as ``-<pid>-``) no longer present in the process table.
* Never reads anything under ``audits\\`` and never makes a network request.

Stdlib only. No third-party packages.

Usage
-----
    python fec/surfaces/glassbox_app_server.py [--port 8823] [--events DIR] [--open]
    python fec/surfaces/glassbox_app_server.py --stop
"""

from __future__ import annotations

import argparse
import calendar
import ctypes
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

APP_NAME = "glassbox-app"
APP_VERSION = "1.0.0"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EVENTS_DIR = os.path.join(
    os.path.expanduser("~"), ".claude", "glassbox", "events"
)
DEFAULT_STATE_DIR = os.path.join(
    os.path.expanduser("~"), ".claude", "glassbox", "state"
)
DEFAULT_PORT = 8823
STATE_FILENAME = "glassbox_app.json"

# ---------------------------------------------------------------------------
# Reaper-mirrored vocabulary. Keep these in lockstep with
# fec/reap_abandoned_glassbox_tasks.ps1 -- the whole point of the abandoned
# lane is that the app and the reaper agree about what is dead.
# ---------------------------------------------------------------------------
LIVE_STATUSES = frozenset(
    ["running", "queued", "dispatched", "pending", "in_progress", "started"]
)
TERMINAL_STATUSES = frozenset(
    [
        "completed",
        "complete",
        "failed",
        "blocked",
        "abandoned",
        "cancelled",
        "needs-review",
    ]
)
FAILED_STATUSES = frozenset(["failed", "fail", "error", "errored"])
COMPLETED_STATUSES = frozenset(["completed", "complete", "success", "passed", "pass"])
RETIRED_STATUSES = frozenset(["abandoned", "cancelled", "canceled"])

# The emitters name files evt-<timestamp>-<tag>-<pid>-<suffix>.json. The reaper
# pulls the PID with exactly this pattern; do not "improve" it.
PID_RE = re.compile(r"-(\d{2,7})-")

TS_RE = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?"
)
FRACTION_RE = re.compile(r"(?<!\d)(\d{1,12})\s*/\s*(\d{1,12})(?!\d)")

CURRENT_KEYS = (
    "current",
    "completed_queries",
    "completed",
    "done",
    "processed",
    "accepted",
    "finished",
    "collected_records",
)
TOTAL_KEYS = ("total", "total_queries", "required", "target", "expected")
RATE_KEYS = ("records_per_minute", "rate", "queries_per_minute", "per_minute")
ETA_KEYS = ("eta_seconds", "eta")
FAILURE_KEYS = ("failure_count", "failures", "failed_count")

# Defense in depth. Producers are told never to write credentials into events;
# the original WinForms viewer redacts anyway, so this one does too.
SENSITIVE_FLAG_RE = re.compile(
    r"(?i)(--?(?:api[-_]?key|access[-_]?token|refresh[-_]?token|token|password|"
    r"passwd|pwd|secret|client[-_]?secret|authorization|cookie|cf[-_]?clearance)"
    r"\s*(?:=|\s)\s*)(?:\"[^\"]*\"|'[^']*'|\S+)"
)
SENSITIVE_ASSIGN_RE = re.compile(
    r"(?i)((?:api[-_]?key|access[-_]?token|refresh[-_]?token|token|password|passwd|"
    r"pwd|secret|client[-_]?secret|authorization|cookie|set[-_]?cookie|"
    r"cf[-_]?clearance)\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^,;\s}\]]+)"
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|token|api_key|apikey|key|sig|signature)=)"
    r"[^&\s]+"
)

MAX_TEXT = 4000
MAX_COMMAND = 6000
FEED_MAXLEN = 4000
HISTORY_MAXLEN = 80
BAD_FILE_ATTEMPTS = 3


def redact(text):
    """Strip credential-looking substrings and clamp length."""
    if not text:
        return ""
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False, default=str)
        except Exception:
            text = str(text)
    out = SENSITIVE_FLAG_RE.sub(r"\1[REDACTED]", text)
    out = SENSITIVE_ASSIGN_RE.sub(r"\1[REDACTED]", out)
    out = BEARER_RE.sub("Bearer [REDACTED]", out)
    out = SENSITIVE_QUERY_RE.sub(r"\1[REDACTED]", out)
    if len(out) > MAX_TEXT:
        out = out[:MAX_TEXT] + " ... [TRUNCATED]"
    return out


def parse_timestamp(value):
    """Epoch seconds for a Glass Box timestamp string, or None.

    Handles '2026-07-27 16:20:15 UTC', '2026-07-26T23:30:00Z' and the
    seven-fractional-digit .NET form '2026-07-25T19:27:11.0789899Z'.
    """
    if not value or not isinstance(value, str):
        return None
    m = TS_RE.match(value)
    if not m:
        return None
    year, month, day, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
    frac = m.group(7) or ""
    try:
        base = calendar.timegm((year, month, day, hour, minute, second, 0, 1, -1))
    except Exception:
        return None
    if frac:
        base += float("0." + frac[:9])
    return base


def as_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, AttributeError):
            return None
    return None


def pick_number(payload, keys):
    for key in keys:
        if key in payload:
            value = as_number(payload.get(key))
            if value is not None:
                return value
    return None


def pick_text(payload, keys):
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value
        else:
            try:
                return json.dumps(value, ensure_ascii=False, default=str)
            except Exception:
                return str(value)
    return ""


# ---------------------------------------------------------------------------
# Live process table
# ---------------------------------------------------------------------------


def _enum_pids_ctypes():
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    size = 4096
    while True:
        buf = (ctypes.c_uint32 * size)()
        needed = ctypes.c_uint32()
        ok = psapi.EnumProcesses(
            ctypes.byref(buf), ctypes.sizeof(buf), ctypes.byref(needed)
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        count = needed.value // ctypes.sizeof(ctypes.c_uint32)
        if count < size:
            return set(int(buf[i]) for i in range(count))
        size *= 2


def _enum_pids_tasklist():
    import subprocess

    out = subprocess.run(
        ["tasklist", "/NH", "/FO", "CSV"],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1:
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def enumerate_live_pids():
    if os.name == "nt":
        try:
            return _enum_pids_ctypes()
        except Exception:
            pass
        try:
            return _enum_pids_tasklist()
        except Exception:
            return set()
    try:
        return set(int(name) for name in os.listdir("/proc") if name.isdigit())
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Event store
# ---------------------------------------------------------------------------


class Row(object):
    """Aggregate state for one (lane, source, run_id) triple."""

    __slots__ = (
        "lane",
        "source",
        "run_id",
        "status",
        "kind",
        "stage",
        "summary",
        "detail",
        "current",
        "total",
        "rate",
        "eta",
        "failures",
        "command",
        "output_directory",
        "artifact",
        "invocation_id",
        "when",
        "event_ts",
        "pid",
        "file",
        "count",
        "first_when",
        "last_error",
        "last_error_when",
        "extra",
    )

    def __init__(self, lane, source, run_id):
        self.lane = lane
        self.source = source
        self.run_id = run_id
        self.status = "unknown"
        self.kind = ""
        self.stage = ""
        self.summary = ""
        self.detail = ""
        self.current = None
        self.total = None
        self.rate = None
        self.eta = None
        self.failures = None
        self.command = ""
        self.output_directory = ""
        self.artifact = ""
        self.invocation_id = ""
        self.when = 0.0
        self.event_ts = None
        self.pid = None
        self.file = ""
        self.count = 0
        self.first_when = None
        self.last_error = ""
        self.last_error_when = None
        self.extra = {}

    @property
    def key(self):
        return "%s|%s|%s" % (self.lane, self.source, self.run_id)


class EventStore(object):
    """Incremental, read-only aggregator over the Glass Box event directory."""

    def __init__(self, events_dir, stale_minutes=20.0):
        self.events_dir = events_dir
        self.stale_seconds = float(stale_minutes) * 60.0
        self.lock = threading.RLock()

        self._fingerprints = {}  # filename -> (mtime_ns, size)
        self._bad_files = {}  # filename -> (fingerprint, attempts)
        self._rows = {}  # key -> Row
        self._history = {}  # key -> deque of compact events
        self._feed = deque(maxlen=FEED_MAXLEN)
        self._feed_seq = 0

        self._live_pids = set()
        self._live_pids_at = 0.0

        # Observability of the observer itself.
        self.stats = {
            "scans": 0,
            "files_seen": 0,
            "files_parsed_total": 0,
            "files_skipped_total": 0,
            "last_scan_files_parsed": 0,
            "last_scan_seconds": 0.0,
            "last_scan_at": None,
            "first_scan_seconds": None,
            "first_scan_files": 0,
            "events_dir_missing": False,
            "last_error": "",
        }

    # -- process table ------------------------------------------------------

    def live_pids(self, max_age=5.0):
        now = time.time()
        if now - self._live_pids_at > max_age or not self._live_pids:
            try:
                self._live_pids = enumerate_live_pids()
            except Exception:
                self._live_pids = set()
            self._live_pids_at = now
        return self._live_pids

    # -- scanning -----------------------------------------------------------

    def scan(self):
        """One incremental pass. Returns (files_seen, files_parsed, seconds)."""
        started = time.perf_counter()
        parsed = 0
        seen = 0
        pending = []

        try:
            with os.scandir(self.events_dir) as it:
                for entry in it:
                    name = entry.name
                    if not name.endswith(".json"):
                        continue
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    seen += 1
                    fp = (st.st_mtime_ns, st.st_size)
                    if self._fingerprints.get(name) == fp:
                        continue
                    bad = self._bad_files.get(name)
                    if bad is not None and bad[0] == fp and bad[1] >= BAD_FILE_ATTEMPTS:
                        continue
                    pending.append((st.st_mtime, name, fp))
            self.stats["events_dir_missing"] = False
        except FileNotFoundError:
            self.stats["events_dir_missing"] = True
            return (0, 0, time.perf_counter() - started)
        except OSError as exc:
            self.stats["last_error"] = "scandir: %s" % exc
            return (0, 0, time.perf_counter() - started)

        # Chronological order so "newest event wins" falls out naturally and
        # the replayed feed reads in the order things actually happened.
        pending.sort(key=lambda item: (item[0], item[1]))

        with self.lock:
            for mtime, name, fp in pending:
                path = os.path.join(self.events_dir, name)
                payload = self._read_event(path, name, fp)
                if payload is None:
                    continue
                self._fingerprints[name] = fp
                self._bad_files.pop(name, None)
                try:
                    self._ingest(payload, name, mtime)
                    parsed += 1
                except Exception as exc:  # never let one bad event kill the scan
                    self.stats["last_error"] = "ingest %s: %s" % (name, exc)

            elapsed = time.perf_counter() - started
            self.stats["scans"] += 1
            self.stats["files_seen"] = seen
            self.stats["files_parsed_total"] += parsed
            self.stats["last_scan_files_parsed"] = parsed
            self.stats["last_scan_seconds"] = elapsed
            self.stats["last_scan_at"] = time.time()
            if self.stats["first_scan_seconds"] is None:
                self.stats["first_scan_seconds"] = elapsed
                self.stats["first_scan_files"] = parsed
        return (seen, parsed, elapsed)

    def _read_event(self, path, name, fp):
        """Read + parse one event file. Torn/partial reads are tolerated.

        Emitters write ``<name>.tmp`` and then move it into place, but a
        move can still be observed mid-flight on Windows. A file that fails
        to parse is left un-fingerprinted so the next poll retries it; after
        BAD_FILE_ATTEMPTS identical failures it is written off so we do not
        re-read genuinely corrupt files forever.
        """
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except (FileNotFoundError, PermissionError, OSError):
            return None
        if not raw:
            self._note_bad(name, fp)
            return None
        try:
            text = raw.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            try:
                text = raw.decode("utf-16", errors="strict")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except (ValueError, RecursionError):
            self._note_bad(name, fp)
            return None
        if not isinstance(payload, dict):
            self._note_bad(name, fp)
            return None
        return payload

    def _note_bad(self, name, fp):
        prev = self._bad_files.get(name)
        attempts = prev[1] + 1 if prev and prev[0] == fp else 1
        self._bad_files[name] = (fp, attempts)
        self.stats["files_skipped_total"] += 1

    # -- aggregation --------------------------------------------------------

    def _ingest(self, payload, filename, mtime):
        lane = (str(payload.get("lane") or "").strip()) or "unknown"
        source = (str(payload.get("source") or "").strip()) or "unknown"
        run_id = (str(payload.get("run_id") or "").strip()) or "unknown"
        key = "%s|%s|%s" % (lane, source, run_id)

        status = (str(payload.get("status") or payload.get("state") or "unknown")).strip()
        kind = str(payload.get("kind") or payload.get("event") or "event").strip()
        stage = str(payload.get("stage") or payload.get("phase") or "").strip()
        summary = redact(pick_text(payload, ("summary", "message", "title")))
        detail = redact(pick_text(payload, ("detail", "description")))

        current = pick_number(payload, CURRENT_KEYS)
        total = pick_number(payload, TOTAL_KEYS)
        if current is None or total is None or total <= 0:
            frac = FRACTION_RE.search(summary or "")
            if frac:
                try:
                    fc = float(frac.group(1))
                    ft = float(frac.group(2))
                    if ft > 0:
                        current, total = fc, ft
                except ValueError:
                    pass
        if total is not None and total <= 0:
            total = None

        rate = pick_number(payload, RATE_KEYS)
        eta = pick_number(payload, ETA_KEYS)
        failures = pick_number(payload, FAILURE_KEYS)

        failure_text = redact(pick_text(payload, ("failure", "error", "exception")))
        event_ts = parse_timestamp(
            payload.get("timestamp_utc")
            or payload.get("updated_at")
            or payload.get("timestamp")
            or payload.get("time")
        )

        pid_match = PID_RE.search(filename)
        pid = int(pid_match.group(1)) if pid_match else None

        compact = {
            "seq": self._feed_seq,
            "when": mtime,
            "ts": event_ts,
            "lane": lane,
            "source": source,
            "run_id": run_id,
            "key": key,
            "status": status,
            "kind": kind,
            "stage": stage,
            "summary": summary,
            "detail": detail,
            "current": current,
            "total": total,
            "rate": rate,
            "eta": eta,
            "failures": failures,
            "failure": failure_text,
            "pid": pid,
            "file": filename,
        }
        self._feed_seq += 1
        self._feed.append(compact)

        hist = self._history.get(key)
        if hist is None:
            hist = deque(maxlen=HISTORY_MAXLEN)
            self._history[key] = hist
        hist.append(compact)

        row = self._rows.get(key)
        if row is None:
            row = Row(lane, source, run_id)
            self._rows[key] = row
        row.count += 1
        if row.first_when is None or mtime < row.first_when:
            row.first_when = mtime

        is_failure = status.lower() in FAILED_STATUSES or bool(failure_text)
        if is_failure and (row.last_error_when is None or mtime >= row.last_error_when):
            row.last_error = failure_text or detail or summary
            row.last_error_when = mtime

        # Newest event wins, judged on file mtime -- same rule as the reaper.
        if mtime < row.when:
            return

        row.when = mtime
        row.event_ts = event_ts
        row.status = status
        row.kind = kind
        row.stage = stage
        row.summary = summary
        row.detail = detail
        row.pid = pid
        row.file = filename
        if current is not None:
            row.current = current
        if total is not None:
            row.total = total
        row.rate = rate
        row.eta = eta
        if failures is not None:
            row.failures = failures

        command = pick_text(payload, ("command", "cmd", "command_line"))
        if command:
            row.command = redact(command)[:MAX_COMMAND]
        out_dir = pick_text(payload, ("output_directory", "output_dir", "out"))
        if out_dir:
            row.output_directory = redact(out_dir)
        artifact = pick_text(payload, ("artifact", "artifacts", "path", "file"))
        if artifact:
            row.artifact = redact(artifact)
        inv = payload.get("invocation_id")
        if inv:
            row.invocation_id = str(inv)[:80]

        extra = {}
        for name in (
            "workers",
            "active_workers",
            "collected_records",
            "resumed_records",
            "attempt_failure_count",
            "recovered_failure_count",
            "transport",
            "retry_waits",
        ):
            if name in payload:
                value = payload.get(name)
                if isinstance(value, (int, float, str, bool)) or value is None:
                    extra[name] = value
        row.extra = extra

    # -- classification -----------------------------------------------------

    def classify(self, row, now, live_pids):
        """Return (state, reapable). Mirrors reap_abandoned_glassbox_tasks.ps1."""
        status = (row.status or "").strip().lower()
        age = max(0.0, now - row.when)
        pid_alive = row.pid is not None and row.pid in live_pids

        is_live_status = status in LIVE_STATUSES
        is_stale = age > self.stale_seconds
        # The reaper's exact predicate.
        reapable = bool(is_live_status and is_stale and not pid_alive)

        if reapable:
            return ("abandoned", True)
        if status in RETIRED_STATUSES:
            return ("abandoned", False)
        if status in FAILED_STATUSES:
            return ("failed", False)
        if status in COMPLETED_STATUSES:
            return ("completed", False)
        if status == "blocked":
            return ("blocked", False)
        if status == "needs-review":
            return ("blocked", False)
        if is_live_status:
            return ("live", False)
        return ("other", False)

    # -- snapshots ----------------------------------------------------------

    def snapshot_rows(self):
        now = time.time()
        live_pids = self.live_pids()
        rows = []
        with self.lock:
            for row in self._rows.values():
                state, reapable = self.classify(row, now, live_pids)
                pct = None
                if row.current is not None and row.total:
                    pct = max(0.0, min(100.0, (row.current / row.total) * 100.0))
                rows.append(
                    {
                        "key": row.key,
                        "lane": row.lane,
                        "source": row.source,
                        "run_id": row.run_id,
                        "status": row.status,
                        "state": state,
                        "reapable": reapable,
                        "kind": row.kind,
                        "stage": row.stage,
                        "summary": row.summary,
                        "detail": row.detail,
                        "current": row.current,
                        "total": row.total,
                        "pct": pct,
                        "rate": row.rate,
                        "eta": row.eta,
                        "failures": row.failures,
                        "command": row.command,
                        "output_directory": row.output_directory,
                        "artifact": row.artifact,
                        "invocation_id": row.invocation_id,
                        "when": row.when,
                        "event_ts": row.event_ts,
                        "age": max(0.0, now - row.when),
                        "duration": (
                            max(0.0, row.when - row.first_when)
                            if row.first_when is not None
                            else None
                        ),
                        "pid": row.pid,
                        "pid_alive": bool(row.pid is not None and row.pid in live_pids),
                        "events": row.count,
                        "last_error": row.last_error,
                        "file": row.file,
                        "extra": row.extra,
                    }
                )
        rows.sort(key=lambda item: item["when"], reverse=True)
        return rows, now

    def summary(self, rows, now):
        by_lane = {}
        by_state = {}
        progress_current = 0.0
        progress_total = 0.0
        active_progress = []
        for row in rows:
            lane = row["lane"]
            state = row["state"]
            lane_bucket = by_lane.setdefault(
                lane, {"lane": lane, "total": 0, "live": 0, "blocked": 0,
                       "completed": 0, "failed": 0, "abandoned": 0, "other": 0}
            )
            lane_bucket["total"] += 1
            lane_bucket[state] = lane_bucket.get(state, 0) + 1
            by_state[state] = by_state.get(state, 0) + 1
            if row["total"] and row["current"] is not None:
                if state in ("live", "blocked"):
                    progress_current += row["current"]
                    progress_total += row["total"]
                    active_progress.append(
                        {
                            "key": row["key"],
                            "source": row["source"],
                            "run_id": row["run_id"],
                            "lane": row["lane"],
                            "state": state,
                            "current": row["current"],
                            "total": row["total"],
                            "pct": row["pct"],
                            "rate": row["rate"],
                            "eta": row["eta"],
                            "summary": row["summary"],
                        }
                    )
        active_progress.sort(key=lambda item: item["pct"] or 0.0)
        lanes = sorted(by_lane.values(), key=lambda item: (-item["total"], item["lane"]))
        stats = dict(self.stats)
        return {
            "now": now,
            "rows_total": len(rows),
            "by_lane": lanes,
            "by_state": by_state,
            "reapable": sum(1 for row in rows if row["reapable"]),
            "overall": {
                "current": progress_current,
                "total": progress_total,
                "pct": (
                    (progress_current / progress_total) * 100.0
                    if progress_total
                    else None
                ),
                "runs": len(active_progress),
            },
            "active_progress": active_progress[:12],
            "stale_minutes": self.stale_seconds / 60.0,
            "events_dir": self.events_dir,
            "scan": stats,
        }

    def feed(self, limit=200, lane=None, source=None, state=None, text=None, since=None):
        limit = max(1, min(1000, int(limit)))
        needle = (text or "").strip().lower()
        out = []
        with self.lock:
            items = list(self._feed)
        for item in reversed(items):
            if since is not None and item["seq"] <= since:
                continue
            if lane and item["lane"] != lane:
                continue
            if source and item["source"] != source:
                continue
            if state and (item["status"] or "").lower() != state:
                continue
            if needle:
                blob = "%s %s %s %s" % (
                    item["source"],
                    item["summary"],
                    item["detail"],
                    item["kind"],
                )
                if needle not in blob.lower():
                    continue
            out.append(item)
            if len(out) >= limit:
                break
        out.reverse()
        return out

    def history(self, key, limit=80):
        with self.lock:
            hist = self._history.get(key)
            items = list(hist) if hist else []
            row = self._rows.get(key)
        items = items[-max(1, min(HISTORY_MAXLEN, int(limit))):]
        detail = None
        if row is not None:
            now = time.time()
            state, reapable = self.classify(row, now, self.live_pids())
            detail = {
                "key": row.key,
                "lane": row.lane,
                "source": row.source,
                "run_id": row.run_id,
                "status": row.status,
                "state": state,
                "reapable": reapable,
                "stage": row.stage,
                "kind": row.kind,
                "summary": row.summary,
                "detail": row.detail,
                "command": row.command,
                "output_directory": row.output_directory,
                "artifact": row.artifact,
                "invocation_id": row.invocation_id,
                "last_error": row.last_error,
                "current": row.current,
                "total": row.total,
                "rate": row.rate,
                "eta": row.eta,
                "failures": row.failures,
                "pid": row.pid,
                "pid_alive": bool(row.pid is not None and row.pid in self.live_pids()),
                "events": row.count,
                "when": row.when,
                "age": max(0.0, now - row.when),
                "extra": row.extra,
            }
        return {"run": detail, "history": items}

    def sources(self):
        with self.lock:
            return sorted(set(row.source for row in self._rows.values()))


# ---------------------------------------------------------------------------
# Self-emission: the app is itself a process on the stream.
# ---------------------------------------------------------------------------


class Emitter(object):
    """Appends this app's own lifecycle events. Never rewrites anything."""

    def __init__(self, events_dir, run_id, lane="system", enabled=True):
        self.events_dir = events_dir
        self.run_id = run_id
        self.lane = lane
        self.enabled = enabled
        self.seq = 0

    def emit(self, kind, status, stage, summary, detail="", current=0, total=0, **extra):
        if not self.enabled:
            return
        payload = {
            "schema_version": 1,
            "timestamp_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC",
            "lane": self.lane,
            "source": "glassbox-app",
            "run_id": self.run_id,
            "kind": kind,
            "status": status,
            "stage": stage,
            "summary": summary,
            "detail": detail,
            "current": current,
            "total": total,
        }
        payload.update(extra)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        name = "evt-%s%03d-gbapp-%d-%d.json" % (
            stamp,
            int((time.time() % 1) * 1000),
            os.getpid(),
            self.seq,
        )
        self.seq += 1
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
        tmp = os.path.join(self.events_dir, name + ".tmp")
        final = os.path.join(self.events_dir, name)
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, final)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "GlassBoxApp/" + APP_VERSION
    protocol_version = "HTTP/1.1"

    store = None
    front_end_path = None
    started_at = 0.0
    shutdown_token = ""
    # staticmethod, so instance access does not bind self as an argument.
    on_shutdown = staticmethod(lambda: None)
    _front_cache = (None, None, None)  # (mtime, size, bytes)
    _front_lock = threading.Lock()

    def log_message(self, fmt, *args):  # quiet by default
        if getattr(self.server, "verbose", False):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers ------------------------------------------------------------

    def _send(self, code, body, content_type, extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False)
        self._send(code, body, "application/json; charset=utf-8")

    def _front_end(self):
        path = self.front_end_path
        try:
            st = os.stat(path)
        except OSError:
            return None
        with Handler._front_lock:
            cached = Handler._front_cache
            if cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
                return cached[2]
            with open(path, "rb") as handle:
                data = handle.read()
            Handler._front_cache = (st.st_mtime_ns, st.st_size, data)
            return data

    # -- routing ------------------------------------------------------------

    def valid_host(self):
        """DNS-rebinding guard: a rebound hostname still arrives with its own name in Host."""
        port = self.server.server_port
        return (self.headers.get("Host") or "").lower() in (
            "127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port)

    def do_GET(self):
        if not self.valid_host():
            self._json({"error": "Local access only."}, 403)
            return
        try:
            parsed = urlparse(self.path)
            route = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            self.route(route, query)
        except Exception:
            # The traceback stays in this process's log; the caller gets nothing to mine.
            sys.stderr.write("[%s] request failed: %s\n%s\n" % (APP_NAME, self.path.split("?", 1)[0], traceback.format_exc()))
            try:
                self._json({"error": "server", "detail": "Internal server error."}, 500)
            except Exception:
                pass

    def route(self, route, query):
        def one(name, default=None):
            values = query.get(name)
            return values[0] if values else default

        if route in ("/", "/index.html", "/app"):
            body = self._front_end()
            if body is None:
                self._send(500, "front end not found", "text/plain; charset=utf-8")
            else:
                self._send(200, body, "text/html; charset=utf-8")
            return

        if route == "/api/health":
            self._json(
                {
                    "ok": True,
                    "app": APP_NAME,
                    "version": APP_VERSION,
                    "pid": os.getpid(),
                    "port": self.server.server_address[1],
                    "uptime": time.time() - self.started_at,
                    "events_dir": self.store.events_dir,
                }
            )
            return

        if route == "/api/rows":
            rows, now = self.store.snapshot_rows()
            self._json({"rows": rows, "summary": self.store.summary(rows, now)})
            return

        if route == "/api/summary":
            rows, now = self.store.snapshot_rows()
            self._json(self.store.summary(rows, now))
            return

        if route == "/api/events":
            since = one("since")
            self._json(
                {
                    "events": self.store.feed(
                        limit=int(one("limit", "200") or 200),
                        lane=one("lane"),
                        source=one("source"),
                        state=one("status"),
                        text=one("q"),
                        since=int(since) if since not in (None, "") else None,
                    ),
                    "sources": self.store.sources(),
                    "now": time.time(),
                }
            )
            return

        if route == "/api/run":
            key = one("key")
            if not key:
                lane = one("lane", "")
                source = one("source", "")
                run_id = one("run_id", "")
                key = "%s|%s|%s" % (lane, source, run_id)
            payload = self.store.history(key, limit=int(one("limit", "80") or 80))
            if payload["run"] is None:
                self._json({"error": "unknown run", "key": key}, 404)
            else:
                self._json(payload)
            return

        if route == "/api/shutdown":
            # Lets the launcher stop the app cleanly so it can write its own
            # terminal event instead of leaving a phantom "running" row behind.
            # The token lives in the state file, so only a local caller that can
            # read this user's profile can trigger it.
            token = one("token", "")
            if not self.shutdown_token or token != self.shutdown_token:
                self._json({"error": "forbidden"}, 403)
                return
            self._json({"ok": True, "stopping": True})
            if callable(self.on_shutdown):
                threading.Thread(target=self.on_shutdown, daemon=True).start()
            return

        self._json({"error": "not found", "path": route}, 404)


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # so a port clash is detected, not silently shared
    verbose = False

    def handle_error(self, request, client_address):
        """Browsers and PowerShell drop keep-alive sockets constantly.

        Those are not faults, so they must not spray tracebacks into the log
        and drown a real one.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError, TimeoutError)):
            return
        if self.verbose:
            ThreadingHTTPServer.handle_error(self, request, client_address)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def find_free_port(preferred, host="127.0.0.1", attempts=40):
    for offset in range(attempts):
        candidate = preferred + offset
        if candidate > 65535:
            break
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError("no free port near %d" % preferred)


def write_state(state_dir, payload):
    try:
        os.makedirs(state_dir, exist_ok=True)
        path = os.path.join(state_dir, STATE_FILENAME)
        tmp = path + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def clear_state(state_dir):
    try:
        os.unlink(os.path.join(state_dir, STATE_FILENAME))
    except Exception:
        pass


def scanner_loop(store, interval, stop_event, emitter, heartbeat_seconds):
    last_heartbeat = time.time()
    while not stop_event.is_set():
        try:
            store.scan()
        except Exception as exc:
            store.stats["last_error"] = "scan: %s" % exc
        if heartbeat_seconds and time.time() - last_heartbeat >= heartbeat_seconds:
            last_heartbeat = time.time()
            rows, now = store.snapshot_rows()
            summary = store.summary(rows, now)
            emitter.emit(
                "app.heartbeat",
                "running",
                "serving",
                "Glass Box app serving %d rows (%d live, %d abandoned)"
                % (
                    summary["rows_total"],
                    summary["by_state"].get("live", 0),
                    summary["by_state"].get("abandoned", 0),
                ),
                detail="Read-only viewer. Poll cost %.3fs for %d files."
                % (
                    store.stats["last_scan_seconds"],
                    store.stats["files_seen"],
                ),
                current=summary["by_state"].get("live", 0),
                total=summary["rows_total"],
            )
        stop_event.wait(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="GLASS BOX app server (read-only)")
    parser.add_argument("--events", default=DEFAULT_EVENTS_DIR)
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--scan-interval", type=float, default=2.0)
    parser.add_argument("--stale-minutes", type=float, default=20.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=300.0)
    parser.add_argument("--no-emit", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--open", action="store_true", help="open a browser")
    parser.add_argument(
        "--front-end", default=os.path.join(HERE, "glassbox_app.html")
    )
    parser.add_argument(
        "--scan-once",
        action="store_true",
        help="scan, print a JSON summary, exit (no server)",
    )
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("refusing to bind to a non-loopback host: %s" % args.host)

    store = EventStore(args.events, stale_minutes=args.stale_minutes)

    if args.scan_once:
        seen, parsed, elapsed = store.scan()
        rows, now = store.snapshot_rows()
        print(
            json.dumps(
                {
                    "first_scan": {
                        "files_seen": seen,
                        "files_parsed": parsed,
                        "seconds": round(elapsed, 4),
                    },
                    "summary": store.summary(rows, now),
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )
        seen2, parsed2, elapsed2 = store.scan()
        print(
            json.dumps(
                {
                    "incremental_scan": {
                        "files_seen": seen2,
                        "files_parsed": parsed2,
                        "seconds": round(elapsed2, 4),
                    }
                },
                indent=2,
            )
        )
        return 0

    port = args.port
    try:
        httpd = AppServer((args.host, port), Handler)
    except OSError:
        port = find_free_port(args.port + 1, args.host)
        httpd = AppServer((args.host, port), Handler)
    httpd.verbose = args.verbose

    run_id = "glassbox-app-%s" % time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    emitter = Emitter(args.events, run_id, lane="system", enabled=not args.no_emit)

    import secrets

    Handler.store = store
    Handler.front_end_path = args.front_end
    Handler.started_at = time.time()
    Handler.shutdown_token = secrets.token_hex(16)
    Handler.on_shutdown = staticmethod(httpd.shutdown)

    url = "http://%s:%d/" % (args.host, port)

    # Prime the store before answering requests so the first page is populated.
    seen, parsed, elapsed = store.scan()
    print("[%s] first scan: %d files, %d parsed, %.3fs" % (APP_NAME, seen, parsed, elapsed))

    seen2, parsed2, elapsed2 = store.scan()
    print(
        "[%s] incremental scan: %d files, %d parsed, %.4fs"
        % (APP_NAME, seen2, parsed2, elapsed2)
    )

    write_state(
        args.state_dir,
        {
            "app": APP_NAME,
            "version": APP_VERSION,
            "pid": os.getpid(),
            "host": args.host,
            "port": port,
            "url": url,
            "events_dir": args.events,
            "started_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC",
            "run_id": run_id,
            "shutdown_token": Handler.shutdown_token,
        },
    )

    emitter.emit(
        "app.started",
        "running",
        "serve",
        "Glass Box app listening on %s" % url,
        detail=(
            "Read-only web viewer over the shared event stream. First scan %.3fs "
            "for %d files; incremental poll %.4fs." % (elapsed, seen, elapsed2)
        ),
        current=0,
        total=0,
        command="python fec/surfaces/glassbox_app_server.py --port %d" % port,
        url=url,
    )

    stop_event = threading.Event()
    scanner = threading.Thread(
        target=scanner_loop,
        args=(store, args.scan_interval, stop_event, emitter, args.heartbeat_seconds),
        name="glassbox-scanner",
        daemon=True,
    )
    scanner.start()

    print("[%s] GLASS BOX ready at %s (pid %d)" % (APP_NAME, url, os.getpid()))
    sys.stdout.flush()

    if args.open:
        import webbrowser

        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        try:
            httpd.server_close()
        except Exception:
            pass
        emitter.emit(
            "app.stopped",
            "completed",
            "shutdown",
            "Glass Box app stopped",
            detail="Viewer closed cleanly; nothing on the event stream was modified.",
        )
        clear_state(args.state_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
