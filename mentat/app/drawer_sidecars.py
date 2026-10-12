"""Persisted sidecars for the Graph drawer's cold reads (perf.md H8, 2026-09-24).

Paul, 2026-09-24: "lets really try to draw down latency, whether it be building the
infrastructure for that fast caching". His rulings bind here: no data or integrity rule
changes, answers stay exactly the same, and speed comes from pre-computation and caching.

A drawer's first open used to scan the whole 1.4 GB Banking payload for its transfer paths, and
json.loads every saved PAC snapshot (312 MB of JSON, about 1.4 GB of Python objects at the peak,
700 MB of it kept for the life of the process). This module keeps what those reads derive, bound
to the content hash of what they read:

  native scan results  native_scan.tables() answers for the pinned report payload's
                       transfer_paths table and for a workbook's 'Downstream Activity'
                       projection. The key is the SHA-256 of the input (report_insights.
                       digest_stamp: the full-content hash the caller already verifies, since
                       graph_data compares the payload digest with its manifest proof before it
                       asks), the SHA-256 of the scanner binary, and the exact request. A changed
                       payload, workbook, request or scanner is never served an old answer.
  snapshot indexes     one per saved PAC snapshot file, named by that file's SHA-256: where each
                       committee/cycle unit sits in the file (byte offset, length, SHA-256 of
                       those bytes) and the facts downstream._read_inventory validates and selects
                       on. downstream reads a unit only when a traversal reaches it.

Integrity is unchanged. Every input is still hashed in full once per process, as before: the
payload by graph_data, each snapshot file here before its index is trusted. A unit's bytes are
hashed again when they are read. Only derived work is kept: the scanner's parse, and the JSON
parse of units nobody reads. A sidecar that fails any check is rebuilt, never trusted.

Storage is mentat/state/drawer-sidecars, which git ignores. Writes are atomic (a temporary file,
then os.replace). A build is single-flight within a process, through an explicit lock per sidecar
(lru_cache is not single-flight, brain DE-026), and across processes, through a proclock O_EXCL lock
file, stale only when its process is gone or its pid was reused, never by its age (proclock's one rule,
review M3/H2). A waiter that times out computes the answer itself and persists nothing. Correctness
never depends on a lock: a sidecar is a deterministic function of its key, so two builders write the
same bytes.

Only inputs under the project's .cache folder or the configured report directory are persisted.
Anything else (tests, scratch copies) is computed exactly as before and kept in memory only.
MENTAT_DRAWER_SIDECARS=0 turns persistence off.

For the prewarm harness (safe in a separate below-normal-priority process):
  build_sidecars(industry)   builds the sidecars one industry's drawer reads, through the entry
                             points the click itself uses, and returns a JSON-safe summary.
  warm_console(industry)     in the console process: the per-process full-content hashes a first
                             open still pays, keeping nothing large in memory.
  python mentat/app/drawer_sidecars.py build [--industries A,B] | status
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid

APP = Path(__file__).resolve().parent
PROJECT = APP.parent
SCHEMA = 1
# A snapshot index is what this file's walker derives, so it is bound to this file's code.
CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
DEFAULT_STATE_ROOT = PROJECT / 'state' / 'drawer-sidecars'
LOCK_WAIT_SECONDS = 120
LOCK_POLL_SECONDS = 0.2
TEMPORARY_MAX_AGE_SECONDS = 60 * 60
NATIVE_PAYLOAD_TABLES = frozenset({'transfer_paths'})
# Only the drawer's projection of the Downstream Activity sheet (explicit columns) is kept. Scans
# whose JSON costs as much to reload as to scan are not: report_facts' workbook tables, and the
# evidence maps' projection with every filing record (filing_records).
NATIVE_SHEETS = frozenset({'Downstream Activity'})
UNKEPT_COLUMNS = frozenset({'filing_records'})
# downstream._read_inventory's message for a snapshot that moves under a read.
CHANGED = 'Saved downstream evidence changed during the read. Refresh.'
CHUNK = 1 << 20
KEY_LIMIT = 1 << 16
VALUE_LIMIT = 16 << 20
ELEMENT_LIMIT = 64 << 20
SMALL_FIELDS = ('cycle', 'committee_id', 'totals')
MEMO_LIMIT = 64
MIN_FREE_BYTES = int(1.5 * 1024 ** 3)   # prewarm.MIN_FREE_BYTES
EMIT_SOURCE = 'mentat-drawer-sidecars'
# proclock (the app's cross-process locks) is looked up by name, not imported by a statement: this
# module is part of graph_data's code identity (response_cache.module_closure follows import
# statements), and the lock code decides who builds, never what a sidecar holds.
proclock = importlib.import_module('proclock')


class Unindexable(Exception):
    """A snapshot file this reader does not index. downstream then reads it the old way."""


# ----------------------------------------------------------------------------- settings
_UNSET = object()
_settings = {'state_root': None, 'persist_under': None, 'emit': True}
_settings_lock = threading.Lock()
_roots = None


def configure(state_root=_UNSET, persist_under=_UNSET, emit=_UNSET):
    """Point the sidecars somewhere else (tests, scratch harnesses). None restores a default.
    Returns the previous settings, so configure(**previous) puts them back."""
    global _roots
    with _settings_lock:
        previous = dict(_settings)
        if state_root is not _UNSET:
            _settings['state_root'] = None if state_root is None else Path(state_root)
        if persist_under is not _UNSET:
            _settings['persist_under'] = None if persist_under is None else tuple(Path(p) for p in persist_under)
        if emit is not _UNSET:
            _settings['emit'] = True if emit is None else bool(emit)
        _roots = None
    return previous


def enabled():
    return os.environ.get('MENTAT_DRAWER_SIDECARS', '').strip().lower() not in ('0', 'off', 'false', 'no')


def state_root():
    return Path(_settings['state_root'] or DEFAULT_STATE_ROOT)


def _persist_roots():
    global _roots
    roots = _roots
    if roots is None:
        configured = _settings['persist_under']
        candidates = list(configured) if configured is not None else [PROJECT / '.cache']
        if configured is None:
            try:
                candidates.append(_report_directory())
            except Exception:  # noqa: BLE001 - an unconfigured folder only means workbooks are not kept
                pass
        resolved = []
        for root in candidates:
            try:
                resolved.append(Path(root).resolve())
            except OSError:
                pass
        roots = _roots = tuple(resolved)
    return roots


def persistable(path):
    """True when a sidecar for this input may be written: persistence is on, and the input is
    under the project's .cache folder or the configured report directory."""
    if not enabled():
        return False
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return any(resolved.is_relative_to(root) for root in _persist_roots())


def _report_directory(report_directory=None):
    if report_directory:
        return Path(report_directory)
    tools = str(PROJECT / 'tools')
    if tools not in sys.path:
        sys.path.append(tools)
    from report_directory import report_directory as configured
    return configured(PROJECT)


def _short(*parts):
    return hashlib.sha256('\x1f'.join(map(str, parts)).encode('utf-8')).hexdigest()[:16]


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _emit(kind, status, summary, **fields):
    """Glass Box event (house rule: every process emits). Never raises."""
    if not _settings['emit']:
        return None
    try:
        if str(APP) not in sys.path:
            sys.path.insert(0, str(APP))
        import glassbox_emit
        return glassbox_emit.emit(kind, status, summary, source=EMIT_SOURCE, **fields)
    except Exception:  # noqa: BLE001 - emission never breaks the process it observes
        return None


# ----------------------------------------------------------------------------- processes
if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcess.argtypes = ()
    _kernel32.SetPriorityClass.restype = wintypes.BOOL
    _kernel32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [('dwLength', wintypes.DWORD), ('dwMemoryLoad', wintypes.DWORD),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def process_started(pid):
    """The creation time of a running process (an int, 0 when it runs but cannot be read), or None
    when no process with this pid is running. It only queries: on Windows os.kill(pid, 0) would
    terminate the process it names, so it is never called there. (proclock.process_started.)"""
    return proclock.process_started(pid)


def free_physical_bytes():
    if os.name != 'nt':
        return os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError(ctypes.get_last_error(), 'GlobalMemoryStatusEx failed')
    return int(status.ullAvailPhys)


# ----------------------------------------------------------------------------- files and locks
def _write(path, header, body):
    """Header line (with the body's length and SHA-256), then the body; atomic. Returns bytes."""
    header = {**header, 'schema': SCHEMA, 'body_bytes': len(body),
              'body_sha256': hashlib.sha256(body).hexdigest()}
    data = _canonical(header).encode('ascii') + b'\n' + body
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex[:12]}.tmp')
    try:
        with open(temporary, 'xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return len(data)


def _read(path, expected):
    """The verified body of a sidecar, or None when it is missing or fails any check."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    cut = raw.find(b'\n')
    if cut < 0:
        return None
    try:
        header = json.loads(raw[:cut])
    except ValueError:
        return None
    body = raw[cut + 1:]
    if not isinstance(header, dict) or header.get('schema') != SCHEMA:
        return None
    if any(header.get(name) != value for name, value in expected.items()):
        return None
    if header.get('body_bytes') != len(body) or header.get('body_sha256') != hashlib.sha256(body).hexdigest():
        return None
    return body


def _prune(folder, prefix, keep):
    """Drop older versions of one sidecar, and temporary files a crashed writer left behind."""
    now = time.time()
    try:
        candidates = list(folder.glob(prefix + '*'))
    except OSError:
        return
    for other in candidates:
        try:
            if other.name.endswith('.json') and other.name != keep:
                other.unlink()
            elif other.name.endswith('.tmp') and now - other.stat().st_mtime > TEMPORARY_MAX_AGE_SECONDS:
                other.unlink()
        except OSError:
            pass


def _lock_path(name):
    return state_root() / 'locks' / (name + '.lock')


def _acquire(path):
    """('ours', lock bytes) with the lock taken; ('held', None) while a live process holds it; or
    ('unavailable', None) when no lock can be taken here (then nothing is persisted). A stale lock is
    removed by proclock.claim. The body keeps the fields this module's earlier releases read (started,
    created, token), so a console still on that code respects it (proclock docstring, Compatibility)."""
    extra = {'started': proclock.own_started(), 'created': time.time(), 'token': uuid.uuid4().hex}
    try:
        body = proclock.claim(path, extra=extra)
    except OSError:
        return 'unavailable', None
    return ('held', None) if body is None else ('ours', body)


def _stale(path):
    """True when the lock at `path` is stale by proclock's rule; False while it is live, or gone."""
    return proclock.is_stale(path)


def _release(path, token):
    """Remove the lock only while it is still ours (token: the bytes _acquire wrote)."""
    proclock.release(path, token)


_locks = {}
_locks_guard = threading.Lock()


def _local(name):
    with _locks_guard:
        lock = _locks.get(name)
        if lock is None:
            lock = _locks[name] = threading.Lock()
        return lock


def _single_flight(name, load, build, store):
    """load() when a verified sidecar is there; otherwise build() once across threads and
    processes and store() it. Returns (value, how), how being 'hit', 'built' or 'unshared'."""
    value = load()
    if value is not None:
        return value, 'hit'
    local = _local(name)
    if not local.acquire(timeout=LOCK_WAIT_SECONDS):
        return build(), 'unshared'   # a stalled builder delays this caller, never blocks it
    try:
        value = load()
        if value is not None:
            return value, 'hit'
        path = _lock_path(name)
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            state, token = _acquire(path)
            if state == 'ours':
                try:
                    value = load()   # another process may have finished just before we locked
                    if value is not None:
                        return value, 'hit'
                    began = time.perf_counter()
                    value = build()
                    if value is not None:
                        _store(name, store, value, time.perf_counter() - began)
                    return value, 'built'
                finally:
                    _release(path, token)
            if state == 'unavailable' or time.monotonic() >= deadline:
                return build(), 'unshared'
            # At least one pause per round, so a lock that is stale but cannot be removed this
            # instant is retried, not spun on.
            time.sleep(LOCK_POLL_SECONDS)
            while time.monotonic() < deadline and path.exists() and not _stale(path):
                time.sleep(LOCK_POLL_SECONDS)
            value = load()
            if value is not None:
                return value, 'hit'
    finally:
        local.release()


def _store(name, store, value, seconds):
    try:
        written = store(value)
    except (OSError, TypeError, ValueError) as error:
        # A full disk or a locked file costs the next open a rebuild, never this answer.
        _emit('mentat.drawer.sidecar', 'failed', f'Drawer sidecar {name} not saved: {type(error).__name__}: {error}',
              stage='store', metadata={'name': name})
        return
    _emit('mentat.drawer.sidecar', 'completed', f'Drawer sidecar {name} saved ({written} bytes; built in {seconds:.1f} s)',
          stage='store', duration_ms=int(seconds * 1000), metadata={'name': name, 'bytes': written})


# ----------------------------------------------------------------------------- native scans
_scanners = OrderedDict()
_scanners_guard = threading.Lock()


def scanner_sha256(binary):
    info = Path(binary).stat()
    key = (str(binary), info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    with _scanners_guard:
        value = _scanners.get(key)
    if value is None:
        with open(binary, 'rb') as handle:
            value = hashlib.file_digest(handle, 'sha256').hexdigest()
        with _scanners_guard:
            _scanners[key] = value
            while len(_scanners) > 4:
                _scanners.popitem(last=False)
    return value


def native_key(binary, file_stamp, requests, max_rows, payload_table):
    """The identity of a kept native scan, or None when this scan is not kept: sidecars off, a
    request other than the drawer's two, an input outside the kept roots, or an input that no
    longer matches its stamp. Never raises: any problem means 'scan exactly as before'."""
    try:
        if not enabled():
            return None
        if payload_table is not None:
            if requests or payload_table not in NATIVE_PAYLOAD_TABLES:
                return None
            kind = 'payload'
        else:
            if not requests or not all(isinstance(r, dict) and r.get('name') in NATIVE_SHEETS for r in requests):
                return None
            if not all(isinstance(r.get('columns'), list) and r['columns'] and not UNKEPT_COLUMNS & set(map(str, r['columns']))
                       for r in requests):
                return None
            kind = 'workbook'
        import report_insights
        file_stamp = tuple(file_stamp)
        if len(file_stamp) != 4 or report_insights.stamp(file_stamp[0]) != file_stamp:
            return None
        if not persistable(file_stamp[0]):
            return None
        request = json.loads(_canonical({'requests': requests, 'max_rows': max_rows,
                                         'payload_table': payload_table, 'protocol': 1}))
        return {'kind': kind, 'input': str(file_stamp[0]), 'input_bytes': file_stamp[3],
                # The full-content hash this process keeps for this stamp. For the payload it is
                # the digest graph_data has just compared with the manifest proof.
                'input_sha256': report_insights.digest_stamp(file_stamp),
                'scanner_sha256': scanner_sha256(binary), 'request': request}
    except Exception:  # noqa: BLE001
        return None


class _ScanFailed(Exception):
    """Carries scan()'s own exception through the single flight, to be raised as it was."""


def native_result(key, scan):
    """native_scan.tables() for key: from its sidecar when one verifies, else scan() once (single
    flight across threads and processes) and kept. scan() errors are raised as they are and never
    kept; a None from scan() (busy workers) is returned and not kept. Should this module's own
    bookkeeping fail, the scan runs exactly as it did before sidecars."""
    def guarded():
        try:
            return scan()
        except BaseException as error:  # noqa: BLE001 - re-raised unchanged below
            raise _ScanFailed(error) from None
    try:
        return _native_result(key, guarded)
    except _ScanFailed as failed:
        raise failed.args[0]
    except Exception as error:  # noqa: BLE001
        _emit('mentat.drawer.sidecar', 'failed', f'Drawer sidecar skipped: {type(error).__name__}: {error}',
              stage='lookup', metadata={'kind': key.get('kind'), 'input': key.get('input')})
        return scan()


def _native_result(key, scan):
    stem = f"{key['kind']}-{_short(key['input'], _canonical(key['request']))}"
    name = f"{stem}-{_short(key['input_sha256'], key['scanner_sha256'])}"
    folder = state_root() / 'native'
    path = folder / (name + '.json')
    expected = json.loads(_canonical({'kind': 'native_scan', 'key': key}))

    def load():
        body = _read(path, expected)
        if body is None:
            return None
        try:
            value = json.loads(body)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def store(value):
        body = json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')
        written = _write(path, {**expected, 'created': time.time(), 'writer_pid': os.getpid()}, body)
        _prune(folder, stem + '-', keep=path.name)
        return written

    return _single_flight(name, load, scan, store)[0]


# ----------------------------------------------------------------------------- snapshot indexes
_indexes = OrderedDict()   # (path, mtime_ns, size) -> (sha256, units)
_memo_guard = threading.Lock()


def forget(path=None, contents=False):
    """Drop what this process remembers about one snapshot file (or all of them). contents=True
    also drops the process's full-content hashes (report_insights.digest_stamp): a file whose bytes
    changed under an unchanged stamp is hashed again."""
    with _memo_guard:
        for key in [k for k in _indexes if path is None or k[0] == str(path)]:
            del _indexes[key]
    if contents:
        import report_insights
        report_insights.digest_stamp.cache_clear()


def file_sha256(path, modified, size):
    """The SHA-256 of a whole snapshot file, once per process per stamp, as the old inventory
    hashed every file it read. It is report_insights.digest_stamp, the memo the rest of the app
    (graph_data, the view cache's disk tier) hashes files through, so no file is hashed twice.
    Raises ValueError(CHANGED) when the file does not carry the stamp, or moves under the read."""
    import report_insights
    version = report_insights.stamp(path)
    if (version[1], version[3]) != (modified, size):
        raise ValueError(CHANGED)
    value = report_insights.digest_stamp(version)
    if report_insights.stamp(path) != version:
        raise ValueError(CHANGED)
    return value


def snapshot_index(path, modified, size):
    """(sha256, units) for one saved snapshot file, stamped (path, mtime_ns, size) as downstream
    stamps it. Each unit: offset, length and sha256 of its bytes, the JSON kind of each top-level
    field, and the raw cycle, committee_id and totals facts downstream selects on. Raises
    Unindexable for a file this reader does not index, OSError or ValueError(CHANGED) when the
    file cannot be read as stamped."""
    key = (str(path), modified, size)
    with _memo_guard:
        value = _indexes.get(key)
    if value is not None:
        return value
    with _local('snapshot-index-memo\x1f' + '\x1f'.join(map(str, key))):
        with _memo_guard:
            value = _indexes.get(key)
        if value is not None:
            return value
        value = _kept_index(path, modified, size) if persistable(path) else _build_index(path, modified, size)
        with _memo_guard:
            _indexes[key] = value
            while len(_indexes) > MEMO_LIMIT:
                _indexes.popitem(last=False)
        return value


def _kept_index(path, modified, size):
    resolved = Path(path).resolve()
    fingerprint = file_sha256(path, modified, size)   # the full-content hash, before any trust
    stem = _short(str(resolved))
    name = f'{stem}-{fingerprint[:32]}'
    folder = state_root() / 'snapshot-index'
    target = folder / (name + '.json')
    expected = json.loads(_canonical({'kind': 'snapshot_index', 'path': str(resolved), 'bytes': size,
                                      'file_sha256': fingerprint, 'code_sha256': CODE_SHA256}))

    def load():
        body = _read(target, expected)
        if body is None:
            return None
        try:
            units = json.loads(body)
        except ValueError:
            return None
        return units if isinstance(units, list) else None

    def build():
        built, units = _build_index(path, modified, size)
        if built != fingerprint:
            raise ValueError(CHANGED)
        return units

    def store(units):
        written = _write(target, {**expected, 'created': time.time(), 'writer_pid': os.getpid()},
                         _canonical(units).encode('ascii'))
        _prune(folder, stem + '-', keep=target.name)
        return written

    units, _how = _single_flight('snapshot-index-' + name, load, build, store)
    return fingerprint, units


def _build_index(path, modified, size):
    """Walk one snapshot file with bounded memory, then hash it and each unit's bytes."""
    with open(path, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (info.st_mtime_ns, info.st_size) != (modified, size):
            raise ValueError(CHANGED)
        units = _walk(handle)
    fingerprint = _hash_units(path, units, size)
    info = os.stat(path)
    if (info.st_mtime_ns, info.st_size) != (modified, size):
        raise ValueError(CHANGED)
    return fingerprint, units


def read_unit(path, modified, size, offset, length, digest):
    """One saved unit, parsed from its own bytes after they are hashed against its index."""
    try:
        with open(path, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if (info.st_mtime_ns, info.st_size) != (modified, size):
                raise ValueError(CHANGED)
            handle.seek(offset)
            raw = handle.read(length)
    except OSError:
        raise ValueError(CHANGED) from None
    if len(raw) != length or hashlib.sha256(raw).hexdigest() != digest:
        forget(path, contents=True)
        raise ValueError(CHANGED)
    return json.loads(raw)


_WHITESPACE = re.compile(r'[ \t\n\r]*')   # json's own whitespace set
_DECODER = json.JSONDecoder()             # json.loads' default decoder


class _Reader:
    """ASCII JSON text read from a binary file in bounded chunks, addressed by byte offset."""

    def __init__(self, handle):
        self.handle, self.text, self.base, self.eof = handle, '', 0, False

    def more(self):
        if self.eof:
            return False
        chunk = self.handle.read(CHUNK)
        if not chunk:
            self.eof = True
            return False
        if not chunk.isascii():
            raise Unindexable('The snapshot is not plain ASCII JSON.')
        self.text += chunk.decode('ascii')
        return True

    def release(self, offset):
        """Forget the text before offset; amortized, so a unit costs no repeated copying."""
        cut = offset - self.base
        if cut > CHUNK:
            self.text = self.text[cut:]
            self.base = offset

    def char(self, offset):
        index = offset - self.base
        while index >= len(self.text):
            if not self.more():
                return ''
        return self.text[index]

    def skip(self, offset):
        while True:
            start = offset - self.base
            if start > len(self.text):
                if not self.more():
                    return offset
                continue
            index = _WHITESPACE.match(self.text, start).end()
            if index < len(self.text) or not self.more():
                return self.base + index

    def value(self, offset, limit):
        """One JSON value parsed at offset: (value, end offset)."""
        while True:
            index = offset - self.base
            try:
                value, end = _DECODER.raw_decode(self.text, index)
            except json.JSONDecodeError as error:
                cut_short = error.pos >= len(self.text) - 32 or error.msg.startswith('Unterminated string')
                if self.eof or not cut_short or len(self.text) - index > limit or not self.more():
                    raise Unindexable(f'The snapshot has no JSON value at byte {offset}.') from None
                continue
            if end >= len(self.text) and self.more():
                continue   # a number that reaches the end of the text may go on
            return value, self.base + end


def _kind(value):
    if isinstance(value, bool):
        return 'bool'
    return {dict: 'dict', list: 'list', str: 'str', int: 'int', float: 'float', type(None): 'null'}.get(type(value), 'other')


def _walk(handle):
    reader = _Reader(handle)
    position = reader.skip(0)
    if reader.char(position) != '[':
        raise Unindexable('The snapshot is not a JSON array.')
    position = reader.skip(position + 1)
    units = []
    if reader.char(position) == ']':
        position += 1
    else:
        while True:
            if reader.char(position) != '{':
                raise Unindexable('A snapshot unit is not a JSON object.')
            unit, end = _walk_unit(reader, position)
            units.append(unit)
            position = reader.skip(end)
            mark = reader.char(position)
            if mark == ',':
                position = reader.skip(position + 1)
            elif mark == ']':
                position += 1
                break
            else:
                raise Unindexable('The snapshot array is malformed.')
    if reader.char(reader.skip(position)) != '':
        raise Unindexable('The snapshot has data after its array.')
    return units


def _walk_unit(reader, start):
    kinds, values = {}, {}
    position = reader.skip(start + 1)
    if reader.char(position) == '}':
        return _facts(start, position + 1, kinds, values), position + 1
    while True:
        if reader.char(position) != '"':
            raise Unindexable('A snapshot unit is malformed.')
        name, position = reader.value(position, KEY_LIMIT)
        position = reader.skip(position)
        if reader.char(position) != ':':
            raise Unindexable('A snapshot unit is malformed.')
        position = reader.skip(position + 1)
        if name in kinds:
            raise Unindexable('A snapshot unit repeats a field.')
        if reader.char(position) == '[' and name not in SMALL_FIELDS:
            position = _walk_array(reader, position)
            kinds[name] = 'list'
        else:
            value, position = reader.value(position, VALUE_LIMIT)
            kinds[name] = _kind(value)
            if name in SMALL_FIELDS:
                values[name] = value
        reader.release(position)
        position = reader.skip(position)
        mark = reader.char(position)
        if mark == ',':
            position = reader.skip(position + 1)
        elif mark == '}':
            return _facts(start, position + 1, kinds, values), position + 1
        else:
            raise Unindexable('A snapshot unit is malformed.')


def _walk_array(reader, start):
    """Every element parsed (so the file is valid JSON throughout) and dropped at once."""
    position = reader.skip(start + 1)
    if reader.char(position) == ']':
        return position + 1
    while True:
        _, position = reader.value(position, ELEMENT_LIMIT)
        reader.release(position)
        position = reader.skip(position)
        mark = reader.char(position)
        if mark == ',':
            position = reader.skip(position + 1)
        elif mark == ']':
            return position + 1
        else:
            raise Unindexable('A snapshot schedule is malformed.')


def _facts(start, end, kinds, values):
    facts = {'offset': start, 'length': end - start, 'kinds': kinds}
    for name in ('cycle', 'committee_id'):
        if name in values:
            facts[name] = values[name]
    totals = values.get('totals')
    if isinstance(totals, dict):
        facts['totals'] = {'disbursements': 'disbursements' in totals,
                           'coverage_end_date': totals.get('coverage_end_date')}
    return facts


def _hash_units(path, units, size):
    """One sequential read: the whole file's SHA-256, and each unit's bytes' SHA-256."""
    whole = hashlib.sha256()
    hashers = [hashlib.sha256() for _ in units]
    spans = [(unit['offset'], unit['offset'] + unit['length']) for unit in units]
    index = position = 0
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            view = memoryview(block)
            whole.update(view)
            end = position + len(block)
            while index < len(spans):
                start, stop = spans[index]
                if start >= end:
                    break
                if stop > position:
                    hashers[index].update(view[max(start, position) - position:min(stop, end) - position])
                if stop > end:
                    break
                index += 1
            position = end
    if position != size or index != len(spans):
        raise ValueError(CHANGED)
    for unit, hasher in zip(units, hashers):
        unit['sha256'] = hasher.hexdigest()
    return whole.hexdigest()


# ----------------------------------------------------------------------------- prewarm hooks
def _workbook(industry, report_directory=None):
    path = Path(str(industry))
    if path.suffix.lower() == '.xlsx':
        return path
    return _report_directory(report_directory) / f'{industry} Industry Spending Overview.xlsx'


def industries(report_directory=None):
    """The industries of the configured report directory, as its Overview workbooks name them."""
    folder = _report_directory(report_directory)
    return sorted(p.name.removesuffix(' Industry Spending Overview.xlsx')
                  for p in folder.glob('* Industry Spending Overview.xlsx') if not p.name.startswith('~$'))


def build_sidecars(industry, report_directory=None, release=True):
    """Build, in this process, every sidecar the Graph drawer reads for one industry, through the
    entry points the click itself uses: downstream.index_inventory (the snapshot indexes; no unit
    is read), graph_data.report_activity (the workbook's Downstream Activity scan) and
    graph_data.report_transfers (the pinned payload's transfer paths, when the workbook's Transfer
    Paths sheet is a partial display). Meant for a separate below-normal-priority process: Banking
    costs about 30 s of mostly native scanning once per payload, then nothing until an input
    changes. release=True empties graph_data's two caches afterwards, so the process keeps no rows.

    Returns {'industry', 'workbook', 'ok', 'seconds', 'steps': [{'step', 'seconds', 'result' or
    'error'}], 'sidecars': {'files', 'bytes'}}. Never raises: a failed step is reported and the
    next step still runs."""
    began = time.perf_counter()
    run_id = f'mentat-drawer-sidecars-{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}-{os.getpid()}'
    summary = {'industry': str(industry), 'ok': True, 'steps': []}
    _emit('mentat.drawer.sidecars', 'running', f'Drawer sidecars for {industry}: started', run_id=run_id, stage='build')
    graph_data = None
    try:
        if str(APP) not in sys.path:
            sys.path.insert(0, str(APP))
        import downstream
        import graph_data
        from report_insights import stamp
        workbook = _workbook(industry, report_directory)
        summary['workbook'] = str(workbook)
        version = stamp(workbook)

        def step(name, function):
            started = time.perf_counter()
            entry = {'step': name}
            try:
                entry['result'] = function()
            except Exception as error:  # noqa: BLE001 - reported per step; the next step still runs
                entry['error'] = f'{type(error).__name__}: {error}'
                summary['ok'] = False
            entry['seconds'] = round(time.perf_counter() - started, 3)
            summary['steps'].append(entry)

        step('snapshot indexes', downstream.index_inventory)
        step('downstream activity', lambda: {'groups': len(graph_data.report_activity(version))})
        step('transfer paths', lambda: {'paths': len(graph_data.report_transfers(version))})
    except Exception as error:  # noqa: BLE001
        summary['ok'] = False
        summary['error'] = f'{type(error).__name__}: {error}'
    finally:
        if release and graph_data is not None:
            graph_data.report_activity.cache_clear()
            graph_data.report_transfers.cache_clear()
        summary['seconds'] = round(time.perf_counter() - began, 3)
        try:
            listing = status()
            summary['sidecars'] = {'files': len(listing['sidecars']), 'bytes': listing['bytes']}
        except OSError:
            pass
        _emit('mentat.drawer.sidecars', 'completed' if summary['ok'] else 'failed',
              f'Drawer sidecars for {industry}: {"ready" if summary["ok"] else "incomplete"} in {summary["seconds"]:.1f} s',
              run_id=run_id, stage='build', duration_ms=int(summary['seconds'] * 1000),
              metadata={'steps': summary['steps'], 'error': summary.get('error')})
    return summary


def warm_console(industry, report_directory=None, activity=False):
    """For the console's own startup warm: the per-process full-content hashes a first drawer open
    still pays - the pinned payload's digest, which graph_data checks against its manifest proof
    (Banking: 1.4 GB, 1.5-2.6 s), and every saved snapshot file (312 MB) - plus the transfer paths
    graph_data keeps (about 22,000 small rows for Banking). activity=True also reads the workbook's
    Downstream Activity, which graph_data keeps in memory (Banking: 135,106 groups). Nothing else
    is kept. Same functions, same caches, only earlier. Returns a summary; never raises."""
    summary = {'industry': str(industry), 'ok': True, 'steps': []}
    try:
        if str(APP) not in sys.path:
            sys.path.insert(0, str(APP))
        import downstream
        import graph_data
        from report_insights import stamp
        version = stamp(_workbook(industry, report_directory))
        steps = [('snapshot indexes', downstream.index_inventory),
                 ('transfer paths', lambda: {'paths': len(graph_data.report_transfers(version))})]
        if activity:
            steps.append(('downstream activity', lambda: {'groups': len(graph_data.report_activity(version))}))
        for name, function in steps:
            started = time.perf_counter()
            entry = {'step': name}
            try:
                entry['result'] = function()
            except Exception as error:  # noqa: BLE001
                entry['error'] = f'{type(error).__name__}: {error}'
                summary['ok'] = False
            entry['seconds'] = round(time.perf_counter() - started, 3)
            summary['steps'].append(entry)
    except Exception as error:  # noqa: BLE001
        summary['ok'] = False
        summary['error'] = f'{type(error).__name__}: {error}'
    return summary


def status():
    """Every sidecar on disk: folder, name, bytes, kind, input and when it was written."""
    root = state_root()
    rows = []
    for folder in ('native', 'snapshot-index'):
        for path in sorted((root / folder).glob('*.json')):
            try:
                with open(path, 'rb') as handle:
                    header = json.loads(handle.readline())
                size = path.stat().st_size
            except (OSError, ValueError):
                continue
            key = header.get('key') if isinstance(header.get('key'), dict) else {}
            rows.append({'folder': folder, 'name': path.name, 'bytes': size, 'kind': header.get('kind'),
                         'input': key.get('input') or header.get('path'), 'created': header.get('created')})
    return {'state_root': str(root), 'sidecars': rows, 'bytes': sum(row['bytes'] for row in rows)}


def _below_normal():
    if os.name == 'nt':
        _kernel32.SetPriorityClass(_kernel32.GetCurrentProcess(), 0x4000)   # BELOW_NORMAL_PRIORITY_CLASS


def main(argv=None):
    parser = argparse.ArgumentParser(description='Graph drawer sidecars. Prints one JSON object.')
    parser.add_argument('op', choices=('build', 'status'))
    parser.add_argument('--industries', default=None, help='Comma-separated industries (default: every report).')
    parser.add_argument('--report-directory', default=None)
    parser.add_argument('--min-free-mb', type=int, default=MIN_FREE_BYTES // 2 ** 20)
    parser.add_argument('--below-normal', action='store_true', help='Lower this process to below-normal priority.')
    args = parser.parse_args(argv)
    if args.op == 'status':
        print(json.dumps(status()))
        return 0
    if args.below_normal:
        _below_normal()
    try:
        names = [n.strip() for n in args.industries.split(',') if n.strip()] if args.industries \
            else industries(args.report_directory)
    except Exception as error:  # noqa: BLE001
        print(json.dumps({'op': 'build', 'ok': False, 'error': f'{type(error).__name__}: {error}'}))
        return 2
    results = []
    for name in names:
        if free_physical_bytes() < args.min_free_mb * 2 ** 20:
            results.append({'industry': name, 'ok': False, 'skipped': 'low_memory'})
            continue
        results.append(build_sidecars(name, args.report_directory))
    print(json.dumps({'op': 'build', 'ok': all(r.get('ok') for r in results), 'results': results,
                      'sidecars': {k: v for k, v in status().items() if k != 'sidecars'}}, default=str))
    return 0 if all(r.get('ok') for r in results) else 1


if __name__ == '__main__':
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    sys.exit(main())
