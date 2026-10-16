"""Shared path/identity and conservative quota accounting; import has no I/O.

Only explicit known ledgers are inspected. Legacy counters are retained and
adopted by a monotone high-water receipt before each admission. Overlapping
windows are held until the latest expiry; duplicate snapshots can overcount.
This coordinates this runtime, not unrelated clients of the same provider key.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3


def policy(project_root=None):
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
    value = json.loads((root / 'tools' / 'api_budget_policy.json').read_text(encoding='utf-8'))
    if value.get('schema_version') != 1:
        raise ValueError('Unsupported API budget path policy.')
    for name in ('host_relative_path', 'legacy_project_relative_path'):
        parts = value.get(name)
        if not isinstance(parts, list) or not parts or any(
            not isinstance(part, str) or part in ('', '.', '..') or '/' in part or '\\' in part or ':' in part
            for part in parts
        ):
            raise ValueError('Invalid API budget path policy.')
    if value.get('host_root_environment') != 'LOCALAPPDATA':
        raise ValueError('Invalid API budget host root policy.')
    return value


def budget_path(project_root=None, *, environment=None, explicit=None):
    environment = os.environ if environment is None else environment
    configured = str(explicit or environment.get('MENTAT_API_BUDGET_DB', '')).strip()
    if configured:
        if explicit is None and not Path(configured).is_absolute():
            raise ValueError('MENTAT_API_BUDGET_DB must be an absolute path.')
        return Path(configured).resolve()
    value = policy(project_root)
    host = str(environment.get(value['host_root_environment'], '')).strip()
    if not host or not Path(host).is_absolute():
        raise ValueError('Set MENTAT_API_BUDGET_DB or an absolute LOCALAPPDATA host root.')
    return Path(host).joinpath(*value['host_relative_path']).resolve()


def legacy_budget_paths(project_root=None, *, environment=None, extra=()):
    environment = os.environ if environment is None else environment
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
    value = policy(root)
    configured = str(environment.get('MENTAT_API_BUDGET_IMPORTS', '')).strip()
    imports = json.loads(configured) if configured else []
    if not isinstance(imports, list) or any(not isinstance(item, str) or not Path(item).is_absolute() for item in imports):
        raise ValueError('MENTAT_API_BUDGET_IMPORTS must list explicit absolute ledger paths.')
    paths = [root.joinpath(*value['legacy_project_relative_path']), *map(Path, imports), *map(Path, extra)]
    return tuple(dict.fromkeys(path.resolve() for path in paths))


def budget_id(key):
    return 'openfec:sha256:' + hashlib.sha256(str(key).strip().encode('utf-8')).hexdigest()[:12]


def legacy_budget_id(key):
    value = 0
    for character in str(key).strip():
        value = (value * 31 + ord(character)) % 1000003
    return 'openfec:' + str(value)


def budget_aliases(key):
    digest = hashlib.sha256(str(key).strip().encode('utf-8')).hexdigest()
    return tuple(dict.fromkeys(('openfec', legacy_budget_id(key), *(
        'openfec:sha256:' + digest[:width] for width in range(12, 65)
    ))))


def source_id(path):
    value = Path(path).resolve().as_posix()
    if os.name == 'nt':
        value = value.lower()
    return hashlib.sha256(('file:' + value).encode('utf-8')).hexdigest()


def ensure_schema(db):
    # Preserve the old three-column table for older read-only/status consumers.
    db.execute('CREATE TABLE IF NOT EXISTS api_request_budget (budget_id TEXT PRIMARY KEY, '
               'window_started_epoch REAL NOT NULL, requests_used INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS api_request_budget_imports ('
               'budget_id TEXT NOT NULL, source_id TEXT NOT NULL, source_budget_id TEXT NOT NULL, '
               'source_window_started_epoch REAL NOT NULL, adopted_requests INTEGER NOT NULL, '
               'PRIMARY KEY(budget_id,source_id,source_budget_id,source_window_started_epoch))')


def counter(row):
    start, used = row
    if (not isinstance(start, (int, float)) or not math.isfinite(start) or start < 0
            or not isinstance(used, (int, float)) or isinstance(used, bool) or not math.isfinite(used)
            or used < 0 or used != math.floor(used) or used > 2147483647):
        raise ValueError('API budget ledger contains invalid usage evidence; admission refused.')
    return float(start), int(used)


def _rows(db, ids):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_request_budget'").fetchone()
    if not exists or not ids:
        return []
    return db.execute('SELECT budget_id,window_started_epoch,requests_used FROM api_request_budget '
                      'WHERE budget_id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()


def reserve(database, identity, requests=1, *, aliases=(), legacy_paths=(), now, cap=1000, window=3600):
    if (not isinstance(requests, int) or isinstance(requests, bool) or requests < 0
            or not isinstance(cap, int) or isinstance(cap, bool) or cap < 1 or not math.isfinite(now) or now < 0
            or not isinstance(window, (int, float)) or not math.isfinite(window) or window <= 0):
        raise ValueError('Invalid API budget reservation.')
    database = Path(database).resolve()
    ids = tuple(dict.fromkeys((identity, *aliases)))
    with closing(sqlite3.connect(database, timeout=60, isolation_level=None)) as db:
        db.execute('PRAGMA busy_timeout=60000')
        db.execute('BEGIN IMMEDIATE')
        try:
            ensure_schema(db)
            row = db.execute('SELECT window_started_epoch,requests_used FROM api_request_budget WHERE budget_id=?', (identity,)).fetchone()
            start, used = counter(row) if row else (None, 0)
            if start is not None and now - start >= window:
                start, used = None, 0
            observations = [(source_id(database), _rows(db, ids), True)]
            for path in dict.fromkeys(Path(p).resolve() for p in legacy_paths):
                if path == database or not path.is_file():
                    continue
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=60)) as legacy:
                    legacy.execute('PRAGMA query_only=ON')
                    observations.append((source_id(path), _rows(legacy, ids), False))
            for origin, rows, own in observations:
                for old_id, old_start, old_used in rows:
                    if own and old_id == identity:
                        continue
                    old_start, old_used = counter((old_start, old_used))
                    if now - old_start >= window:
                        continue
                    previous = db.execute('SELECT adopted_requests FROM api_request_budget_imports '
                        'WHERE budget_id=? AND source_id=? AND source_budget_id=? AND source_window_started_epoch=?',
                        (identity, origin, old_id, old_start)).fetchone()
                    adopted = counter((old_start, previous[0]))[1] if previous else 0
                    delta = max(0, old_used - adopted)
                    if delta:
                        used += delta
                        start = old_start if start is None else max(start, old_start)
                    db.execute('INSERT INTO api_request_budget_imports VALUES (?,?,?,?,?) '
                        'ON CONFLICT(budget_id,source_id,source_budget_id,source_window_started_epoch) '
                        'DO UPDATE SET adopted_requests=MAX(adopted_requests,excluded.adopted_requests)',
                        (identity, origin, old_id, old_start, max(adopted, old_used)))
            start = now if start is None else start
            admitted = requests == 0 or used + requests <= cap
            if admitted:
                used += requests
            db.execute('INSERT INTO api_request_budget VALUES (?,?,?) ON CONFLICT(budget_id) '
                       'DO UPDATE SET window_started_epoch=excluded.window_started_epoch,requests_used=excluded.requests_used',
                       (identity, start, used))
            db.commit()
            return dict(admitted=admitted, requests_used=used, requests_remaining=max(0, cap-used), window_started_epoch=start)
        except BaseException:
            db.rollback()
            raise
