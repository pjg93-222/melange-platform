# -*- coding: utf-8 -*-
"""Emit Glass Box events from Python producers.

House rule: every process emits to the shared stream or it is not acceptable.
The pipeline chain was the largest violator -- it wrote only to stdout, so a
twenty-minute production run was invisible while it rewrote the rank authority.

The stream is a directory of single-object JSON files. Filenames carry the
process id, so concurrent producers never collide on a name, and writes are
atomic (temp file in the same directory, then os.replace) so a viewer polling
the directory never reads a half-written event.

Emission must never be able to break the thing it observes: every failure here
is swallowed. An unwritable event is a lost line in a dashboard, not a lost
pipeline run.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
import uuid

EVENTS_DIR = pathlib.Path(
    os.environ.get("GLASSBOX_EVENTS_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "glassbox", "events")
)
SCHEMA_VERSION = 1


def _stamp() -> str:
    t = time.gmtime()
    frac = f"{time.time() % 1:.7f}"[2:9]
    return time.strftime("%Y%m%dT%H%M%S", t) + frac + "Z"


def _iso() -> str:
    t = time.gmtime()
    frac = f"{time.time() % 1:.7f}"[1:9]
    return time.strftime("%Y-%m-%dT%H:%M:%S", t) + frac + "Z"


def emit(
    kind: str,
    status: str,
    summary: str,
    *,
    run_id: str = "",
    correlation_id: str = "",
    parent_id: str = "",
    lane: str = "claude",
    source: str = "moneyball-pipeline",
    stage: str = "",
    detail: str = "",
    duration_ms: int = -1,
    metadata: dict | None = None,
    current: int = -1,
    total: int = -1,
) -> str | None:
    """Write one event. Returns its id, or None if emission failed.

    ``current``/``total`` describe position within a known-length sequence of
    stages. They are omitted entirely when negative, so an existing caller that
    does not know its position emits exactly the payload it emitted before.
    """
    try:
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        eid = f"evt-{_stamp()}-{uuid.uuid4().hex[:10]}"
        payload = {
            "schema_version": SCHEMA_VERSION,
            "id": eid,
            "timestamp_utc": _iso(),
            "run_id": run_id,
            "correlation_id": correlation_id,
            "parent_id": parent_id,
            "lane": lane,
            "source": source,
            "kind": kind,
            "status": status,
            "stage": stage,
            "summary": summary[:400],
            "detail": detail[:2000],
            "cwd": os.getcwd(),
            "duration_ms": int(duration_ms),
            "bytes": -1,
            "metadata": metadata,
        }
        if int(current) >= 0:
            payload["current"] = int(current)
        if int(total) >= 0:
            payload["total"] = int(total)
        final = EVENTS_DIR / f"{eid}.json"
        tmp = EVENTS_DIR / f".{eid}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n")
        os.replace(tmp, final)
        return eid
    except Exception:
        # Observation must never break the observed process.
        return None


def new_run_id(prefix: str = "moneyball") -> str:
    return f"{prefix}-{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}"


def new_correlation_id() -> str:
    return f"corr-{_stamp()}-{uuid.uuid4().hex[:10]}"
