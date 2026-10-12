"""Access ledger: how much each cached answer is worth keeping, shared by every process of the app.

Why. Paul, 2026-09-24: raise the response-cache caps and keep efficiency by ranking what is kept the way
the Replication-Aware Caching Framework does (RACF, sections 3.4-3.6): keep a copy by a multi-factor value
(access frequency with temporal decay, rebuild cost, size, available room) and evict the lowest-value
copies first. Version tracking is not this module's job and does not change: every cache key already
carries the content SHA-256 of its inputs and the fingerprint of its code, so a changed input or changed
code is a different key, never a stale copy.

What is recorded. record(kind, key, body, cost_s, size) notes one access of (kind, key). The cache layers
record their own entries under 'store/<namespace>' (contribution_query_store) and 'cache/<namespace>'
(response_cache), keyed by the entry's file key, with no body. The console and the warmer record request
kinds with the request `body`, so a planner can re-issue what people ask for (top()). cost_s is the
measured seconds to rebuild the answer and size its stored bytes; each keeps its latest measured value
(None keeps the last one).

Value. With half-life H = 7 days and lambda = ln 2 / H, an access at time t contributes
exp(-lambda * (now - t)) to the decayed frequency:
    F(now) = sum over accesses i of exp(-lambda * (now - t_i))
    score  = F(now) * max(cost_s, 0.05) / max(size / 2**20, 0.01)
A missing cost counts as 0.05 s and a missing size as 0.01 MiB. Frequent, recent, expensive to rebuild
and small is worth most; stale, cheap and large goes first. F is stored as (freq, last_ts), the decayed
count as of last_ts, so it is exact without keeping each access: merging (f1, t1) and (f2, t2) at
T = max(t1, t2) gives f1 * exp(-lambda (T - t1)) + f2 * exp(-lambda (T - t2)).

Storage. SQLite at state/prewarm/access.sqlite (PATH; configure() points a test or sandbox elsewhere),
WAL journal, BUSY_TIMEOUT_SECONDS busy timeout, one connection per thread. Several processes share it:
each flush merges inside one BEGIN IMMEDIATE transaction, so no access is lost to a concurrent writer.
record() only updates an in-memory buffer; a daemon thread flushes it every FLUSH_SECONDS, sooner once
FLUSH_RECORDS accesses wait, and at exit. A failed flush is logged and its accesses dropped. record(),
score(), scores() and top() never raise. MENTAT_ACCESS_STATS=0 makes record() a no-op (reads still answer).
Rows unused for FORGET_SECONDS (about 17 half-lives: F below 1e-5) are deleted by a flush once an hour.
"""
import atexit
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import threading
import time

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT / 'state' / 'prewarm' / 'access.sqlite'
PATH = DEFAULT_PATH
HALF_LIFE_SECONDS = 7 * 24 * 3600
DECAY = math.log(2) / HALF_LIFE_SECONDS
MIN_COST_SECONDS = 0.05
MIN_SIZE_MIB = 0.01
MIB = 1024 * 1024
FLUSH_RECORDS = 64
FLUSH_SECONDS = 2.0
BUSY_TIMEOUT_SECONDS = 5.0
MAX_BUFFERED_KEYS = 20000
MAX_BODY_BYTES = 256 * 1024
FORGET_SECONDS = 120 * 24 * 3600
GC_SECONDS = 3600
SCHEMA = ('CREATE TABLE IF NOT EXISTS access(kind TEXT NOT NULL, key TEXT NOT NULL, freq REAL NOT NULL, '
          'last_ts REAL NOT NULL, count INTEGER NOT NULL, cost_s REAL, size INTEGER, body TEXT, '
          'PRIMARY KEY (kind, key)) WITHOUT ROWID')

_log = logging.getLogger('mentat.access')
_lock = threading.Lock()
_buffer = {}          # (kind, key) -> [freq, last_ts, count, cost_s, size, body text]
_waiting = 0          # accesses buffered since the last flush
_wake = threading.Event()
_thread = None
_local = threading.local()
_opened = []          # every connection of this generation, so configure() can close them all
_generation = 0
_last_gc = 0.0
_warned = set()


def enabled():
    """Whether record() records (MENTAT_ACCESS_STATS=0 turns it off)."""
    return os.environ.get('MENTAT_ACCESS_STATS', '1').strip() != '0'


def configure(path=None):
    """Point the ledger at `path` (default DEFAULT_PATH). Buffered accesses go to the old ledger first;
    every thread's connection is closed (a test's temporary folder can then be removed)."""
    global PATH, _generation
    flush()
    with _lock:
        PATH = Path(path) if path is not None else DEFAULT_PATH
        _generation += 1
        opened = list(_opened)
        _opened.clear()
    for db in opened:
        try:
            db.close()
        except sqlite3.Error:
            pass
    return PATH


def is_default():
    """Whether this process records into the live ledger."""
    return os.path.normcase(os.path.abspath(PATH)) == os.path.normcase(os.path.abspath(DEFAULT_PATH))


def _warn(topic, message, *args):
    if topic not in _warned:
        _warned.add(topic)
        _log.warning('access stats: ' + message, *args)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _integer(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value >= 0 else None


def _body_text(body):
    try:
        text = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        _warn('body', 'a request body that is not JSON was recorded without its body')
        return None
    if len(text.encode('utf-8')) > MAX_BODY_BYTES:
        _warn('body-size', 'a request body over %d bytes was recorded without its body', MAX_BODY_BYTES)
        return None
    return text


def _merge_row(new, old):
    """Merge two (freq, last_ts, count, cost_s, size, body) rows; `new` holds the later measurements."""
    reference = max(new[1], old[1])
    freq = new[0] * math.exp(-DECAY * (reference - new[1])) + old[0] * math.exp(-DECAY * (reference - old[1]))
    return [freq, reference, new[2] + old[2], new[3] if new[3] is not None else old[3],
            new[4] if new[4] is not None else old[4], new[5] if new[5] is not None else old[5]]


def value(freq, last_ts, cost_s, size, now=None):
    """The score formula (module docstring) of one stored row."""
    now = time.time() if now is None else now
    decayed = freq * math.exp(-DECAY * max(0.0, now - last_ts))
    return decayed * max(cost_s or 0.0, MIN_COST_SECONDS) / max((size or 0) / MIB, MIN_SIZE_MIB)


def record(kind, key, body=None, cost_s=None, size=None):
    """Note one access of (kind, key). Never raises; costs one dict update under a lock."""
    global _waiting
    try:
        if not enabled():
            return
        kind, key, now = str(kind), str(key), time.time()
        row = [1.0, now, 1, _number(cost_s), _integer(size), _body_text(body) if body is not None else None]
        with _lock:
            old = _buffer.get((kind, key))
            if old is not None:
                row = _merge_row(row, old)
            elif len(_buffer) >= MAX_BUFFERED_KEYS:
                _warn('buffer', 'the buffer is full (flushes are failing); accesses are being dropped')
                return
            _buffer[(kind, key)] = row
            _waiting += 1
            wake = _waiting >= FLUSH_RECORDS
        _start()
        if wake:
            _wake.set()
    except Exception as error:  # noqa: BLE001 - the ledger never breaks the request that records
        _warn('record', 'could not record an access: %s', error)


def _start():
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name='mentat-access-stats', daemon=True)
            _thread.start()


def _run():
    while True:
        _wake.wait(FLUSH_SECONDS)
        _wake.clear()
        flush()


def _connection(path, create):
    """This thread's connection to the ledger at `path`; None when it does not exist and create is False."""
    path = Path(path)
    connections = getattr(_local, 'connections', None)
    if connections is None:
        connections = _local.connections = {}
    slot = (str(path), _generation)
    db = connections.get(slot)
    if db is not None:
        return db
    if not create and not path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    # Used only by the thread that opened it; check_same_thread=False only lets configure() close it.
    db = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None, check_same_thread=False)
    try:
        db.execute(f'PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=NORMAL')
        db.execute(SCHEMA)
    except BaseException:
        db.close()
        raise
    for stale in [item for item in connections if item[1] != _generation]:
        del connections[stale]    # closed by configure()
    connections[slot] = db
    with _lock:
        _opened.append(db)
    return db


def flush():
    """Merge the buffered accesses into the ledger now. False when they were dropped (logged)."""
    global _waiting, _last_gc
    with _lock:
        if not _buffer:
            return True
        batch, path = dict(_buffer), PATH
        _buffer.clear()
        _waiting = 0
    try:
        db = _connection(path, True)
        now = time.time()
        db.execute('BEGIN IMMEDIATE')
        try:
            for (kind, key), row in batch.items():
                old = db.execute('SELECT freq, last_ts, count, cost_s, size, body FROM access WHERE kind=? AND key=?',
                                 (kind, key)).fetchone()
                if old is not None:
                    row = _merge_row(row, list(old))
                db.execute('INSERT OR REPLACE INTO access(kind, key, freq, last_ts, count, cost_s, size, body) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (kind, key, *row))
            collect = now - _last_gc >= GC_SECONDS
            if collect:
                db.execute('DELETE FROM access WHERE last_ts < ?', (now - FORGET_SECONDS,))
            db.execute('COMMIT')
        except BaseException:
            try:
                db.execute('ROLLBACK')
            except sqlite3.Error:
                pass
            raise
        if collect:
            _last_gc = now
        return True
    except Exception as error:  # noqa: BLE001 - logged and dropped, by contract
        _log.warning('access stats: dropped %d buffered accesses: %s', len(batch), error)
        return False


def _rows(kind, keys):
    """{key: [freq, last_ts, count, cost_s, size, body]} of `kind` (only `keys` when given), this process's
    buffered accesses merged in."""
    rows = {}
    db = _connection(PATH, False)
    if db is not None:
        if keys is not None and len(keys) <= 32:
            cursor = (db.execute('SELECT key, freq, last_ts, count, cost_s, size, body FROM access '
                                 'WHERE kind=? AND key=?', (kind, key)).fetchone() for key in keys)
        else:
            cursor = db.execute('SELECT key, freq, last_ts, count, cost_s, size, body FROM access WHERE kind=?', (kind,))
        for found in cursor:
            if found is not None:
                rows[found[0]] = list(found[1:])
    with _lock:
        for (buffered_kind, key), row in _buffer.items():
            if buffered_kind == kind and (keys is None or key in keys):
                rows[key] = _merge_row(list(row), rows[key]) if key in rows else list(row)
    return rows


def scores(kind, keys=None, now=None):
    """{key: score} of every recorded key of `kind` (or of `keys`); {} when the ledger cannot be read."""
    try:
        keys = None if keys is None else set(map(str, keys))
        now = time.time() if now is None else now
        return {key: value(row[0], row[1], row[3], row[4], now) for key, row in _rows(str(kind), keys).items()}
    except Exception as error:  # noqa: BLE001 - an unreadable ledger ranks nothing; callers fall back
        _warn('read', 'could not read the ledger: %s', error)
        return {}


def score(kind, key):
    """The score of (kind, key); 0.0 when it is unknown or the ledger cannot be read."""
    return scores(kind, [key]).get(str(key), 0.0)


def top(kinds=None, limit=50):
    """The highest-scoring entries, highest first: [{kind, key, body, score, count, last_utc, cost_s, size}].
    kinds None is every kind (the cache layers' 'store/...' and 'cache/...' rows too; their body is None)."""
    try:
        flush()
        db = _connection(PATH, False)
        if db is None:
            return []
        columns = 'SELECT kind, key, freq, last_ts, count, cost_s, size, body FROM access'
        if kinds is None:
            cursor = db.execute(columns)
        else:
            kinds = [str(kind) for kind in kinds]
            if not kinds:
                return []
            cursor = db.execute(columns + ' WHERE kind IN (' + ','.join('?' * len(kinds)) + ')', kinds)
        now = time.time()
        ranked = sorted(((value(freq, last, cost, size, now), kind, key, last, count, cost, size, body)
                         for kind, key, freq, last, count, cost, size, body in cursor),
                        key=lambda row: (-row[0], row[1], row[2]))[:max(0, int(limit))]
        return [{'kind': kind, 'key': key, 'body': json.loads(body) if body is not None else None, 'score': rank,
                 'count': count, 'last_utc': datetime.fromtimestamp(last, timezone.utc).isoformat(),
                 'cost_s': cost, 'size': size}
                for rank, kind, key, last, count, cost, size, body in ranked]
    except Exception as error:  # noqa: BLE001
        _warn('read', 'could not read the ledger: %s', error)
        return []


@atexit.register
def _at_exit():
    try:
        flush()
    except Exception:  # noqa: BLE001
        pass
