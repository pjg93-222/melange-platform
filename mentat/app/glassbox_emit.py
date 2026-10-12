"""Emit Glass Box events from Mentat processes (the prewarm harness and the console's startup warm).

House rule (Paul): every process emits progress to the shared stream, or it is not acceptable.

On-disk format, matched to what is already in C:/Users/pgoff/.claude/glassbox/events and to the
existing Python producer (Melange Systems fec/surfaces/glassbox_emit.py):
  * one single-object JSON file per event, named  evt-<yyyymmddTHHMMSS><7 digits>Z-<10 hex>.json
  * schema_version 1, and the same field set: id, timestamp_utc, run_id, correlation_id, parent_id,
    lane, source, kind, status, stage, summary, detail, cwd, duration_ms, bytes, metadata; `current`
    and `total` are added only when known (non-negative), exactly as the existing producer does
  * UTF-8 without a BOM, written to a temporary name in the same directory and moved into place with
    os.replace, so a reader polling the directory never sees half an event.

The 10 hex characters are this process's pid (6 hex) followed by a per-process counter (4 hex), so a
file name is unique per process and per event: two processes never produce the same name, and one
process never repeats a name, even within one clock tick.

Emission must never break the process it observes: every failure here is swallowed and returns None.
"""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import threading
import time

EVENTS_DIR = Path(os.environ.get('GLASSBOX_EVENTS_DIR', r'C:\Users\pgoff\.claude\glassbox\events'))
SCHEMA_VERSION = 1
LANE = 'claude'
SOURCE = 'mentat-prewarm'
# emit.ps1's status vocabulary. Callers use these; other values are written as given.
STATUSES = ('queued', 'running', 'completed', 'failed', 'blocked', 'skipped', 'waiting', 'info')

_counter = itertools.count()
_counter_lock = threading.Lock()


def _stamp(now):
    return time.strftime('%Y%m%dT%H%M%S', time.gmtime(now)) + f'{now % 1:.7f}'[2:9] + 'Z'


def _iso(now):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(now)) + f'{now % 1:.7f}'[1:9] + 'Z'


def _suffix():
    with _counter_lock:
        count = next(_counter)
    return f'{os.getpid() & 0xFFFFFF:06x}{count & 0xFFFF:04x}'


def emit(kind, status, summary, *, run_id='', stage='', detail='', current=-1, total=-1,
         lane=LANE, source=SOURCE, correlation_id='', parent_id='', duration_ms=-1,
         metadata=None, events_dir=None):
    """Write one event and return its id, or None when it could not be written."""
    try:
        directory = Path(events_dir) if events_dir is not None else EVENTS_DIR
        directory.mkdir(parents=True, exist_ok=True)
        now = time.time()
        identifier = f'evt-{_stamp(now)}-{_suffix()}'
        payload = {
            'schema_version': SCHEMA_VERSION,
            'id': identifier,
            'timestamp_utc': _iso(now),
            'run_id': str(run_id or ''),
            'correlation_id': str(correlation_id or ''),
            'parent_id': str(parent_id or ''),
            'lane': str(lane),
            'source': str(source),
            'kind': str(kind),
            'status': str(status),
            'stage': str(stage or ''),
            'summary': str(summary)[:400],
            'detail': str(detail or '')[:2000],
            'cwd': os.getcwd(),
            'duration_ms': int(duration_ms),
            'bytes': -1,
            'metadata': {**(metadata or {}), 'pid': os.getpid()},
        }
        if int(current) >= 0:
            payload['current'] = int(current)
        if int(total) >= 0:
            payload['total'] = int(total)
        final = directory / f'{identifier}.json'
        pending = directory / f'.{identifier}.{os.getpid()}.tmp'
        pending.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding='utf-8', newline='\n')
        os.replace(pending, final)
        return identifier
    except Exception:
        # Observation must never break the observed process.
        return None


def new_run_id(prefix='mentat-prewarm'):
    return f'{prefix}-{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}-{os.getpid()}'
