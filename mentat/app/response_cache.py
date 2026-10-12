"""Shared fast cache for encoded responses: a memory LRU bounded by bytes, a disk tier
that survives restarts, and one build per key at a time across threads and processes.

What an entry is. The exact bytes a route sends (the body; json_body() is server.py
Handler.send's JSON encoding) kept pre-compressed with gzip level 1 and mtime 0, which is
byte for byte what Handler.send's own gzip of that body is, plus a strong ETag taken from
the body's SHA-256. A hit never re-encodes or re-compresses.

What a key is. sha256 of the canonical JSON of {format, namespace, key_parts, deps}.
key_parts names the question; deps must carry the full identity of everything the answer
depends on: the stamps and content hashes of every input the computation reads and the
identity of the code that computes it (code_identity()). A changed input or changed code
is therefore a different key and is never answered with an old entry; old keys age out of
the LRU.

Tiers. Memory: one LRU over all namespaces holding the gzip bytes, bounded by MEMORY_BYTES
(the body is decompressed on demand). Disk: off until configure() names a root. The
console calls configure() in main(); tests, the warmer and other helper processes stay
memory-only unless they configure a root of their own. Layout under the root:
<namespace>/<key>.gz is the gzip body and <key>.json a small meta record (sizes and SHA-256
of both representations, the ETag, when and by which process it was built, and key_parts
and deps for audit). Each file is written to a temporary file in the same folder and moved
into place with os.replace, the body before the meta. An entry is served only when the gzip
read back has the size and SHA-256 its meta records and decompresses to the recorded body
size; a missing, truncated or corrupt entry, or one of another format, is a miss that is
rebuilt and never served. Each namespace is capped at DISK_BYTES (DISK_LIMITS per
namespace); the meta file's mtime records use.

Eviction by value (RACF, access_stats). Over its cap, a namespace drops entries in this order:
first the ones the warmer wrote that no request has used (a meta mtime in WARM_MTIME_NS, the
warmer's demoted range; any disk hit touches the meta and moves it out), oldest first; then the
lowest access_stats score of ('cache/<namespace>', key) (decayed hit frequency x rebuild seconds /
size), with ties and an unreadable ledger least recently used first; entries used within
FRESH_SECONDS last, since their accesses may still be buffered in another process. Every hit and
build is recorded there (never for a root outside the live state tree unless the ledger was pointed
elsewhere too). access_stats is looked up by name, not imported by a statement, so it is not part
of any code identity: it decides what is kept, never what an answer is.

Single flight. One build per key at a time: an explicit per-key in-process flight
(functools.lru_cache memoizes, it does not serialise entry: brain DE-026) and, across
processes, a proclock lock file created with O_CREAT|O_EXCL beside the entry. A lock is
stale, and is removed, by proclock's one rule, the rule of every cross-process lock of the
app (review M3/H2): its process is gone or its pid was recycled, never its age; or it names
this process and this process does not hold it (the in-process flight admits one builder).
A process that finds a live lock waits for the entry (up to WAIT_SECONDS, then
StillBuilding) instead of computing the same key a second time. proclock is looked up by
name, like access_stats, so it is not part of any code identity: it decides who builds,
never what an answer is.
"""
from collections import OrderedDict
from datetime import datetime, timezone
import ast
import gzip as _gzip
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import zlib

FORMAT = 1
PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT / 'state' / 'response-cache'
# None: no disk tier. configure() sets it; a sandbox may point it elsewhere.
ROOT = None
MEMORY_BYTES = 128 * 1024 * 1024
DISK_BYTES = 1024 * 1024 * 1024
DISK_LIMITS = {}
WAIT_SECONDS = 120
# Handler.send compresses with level 1 and mtime 0, so a stored gzip is exactly its bytes.
GZIP_LEVEL = 1
GZIP_MIN_BYTES = 2048
TOUCH_SECONDS = 600
ORPHAN_SECONDS = 3600
FRESH_SECONDS = 300
# prewarm.DEMOTED_BASE_NS and DEMOTED_CEILING_NS: the mtimes the warmer gives what it wrote.
WARM_MTIME_NS = (946684800 * 10 ** 9, 946684800 * 10 ** 9 + 40000 * 10 ** 12)
STILL_BUILDING = 'This view is still being prepared. Retry shortly.'

_NAMESPACE = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
_KEY = re.compile(r'[0-9a-f]{64}')
_COUNTERS = ('memory_hits', 'disk_hits', 'builds', 'kept_out', 'waits', 'corrupt',
             'disk_writes', 'disk_errors', 'memory_evictions', 'disk_evictions')
_log = logging.getLogger('mentat.cache')
# Looked up by name, not imported by a statement: module_closure follows import statements, and
# the lock code decides who builds, never what an answer is (module docstring, Single flight).
proclock = importlib.import_module('proclock')
_mutex = threading.RLock()
_memory = OrderedDict()   # (namespace, key) -> _Record
_memory_used = 0
_flights = {}             # key -> _Flight
_counters = {}            # namespace -> {counter: int}
_digests = OrderedDict()  # file state -> sha256, see file_sha256()
_parsed = {}              # file state -> names it imports, see _imports()
_code = {}                # root module -> _Code
_code_lock = threading.Lock()
_IMPORTED_NS = time.time_ns()
_started_ns = None


class StillBuilding(ValueError):
    """Another thread or process is still building this key. The message is the one
    ViewCache's own wait gives, so a route answers it the same way."""


class CorruptEntry(ValueError):
    """A cached entry did not decompress to the body its meta records."""


def configure(root=None, *, memory_bytes=None, disk_bytes=None):
    """Turn the disk tier on at `root` (default DEFAULT_ROOT); optionally resize the tiers."""
    global ROOT, MEMORY_BYTES, DISK_BYTES
    with _mutex:
        ROOT = Path(root) if root is not None else DEFAULT_ROOT
        if memory_bytes is not None:
            MEMORY_BYTES = int(memory_bytes)
        if disk_bytes is not None:
            DISK_BYTES = int(disk_bytes)
        _trim_memory()
    return ROOT


def enabled():
    """Whether the disk tier is on in this process."""
    return ROOT is not None


def json_body(value):
    """server.py Handler.send's JSON bytes for `value`, exactly."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def _refuse(value):
    raise TypeError(f'{type(value).__name__} cannot be part of a cache key')


def canonical(value):
    """Canonical JSON text: sorted keys, no whitespace, ASCII, finite numbers only."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False, default=_refuse)


def entry_key(namespace, key_parts, deps):
    if not isinstance(namespace, str) or not _NAMESPACE.fullmatch(namespace):
        raise ValueError('A cache namespace is lowercase letters, digits and dashes.')
    text = canonical({'format': FORMAT, 'namespace': namespace, 'key_parts': key_parts, 'deps': deps})
    return hashlib.sha256(text.encode('ascii')).hexdigest()


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


class Entry:
    """One answer. `body` and `gzip` are the exact bytes to send (whichever is not held is
    derived on first use); `value` is the computed object only when this call built it
    (hit == 'built'), so its builder never has to decode its own answer."""
    __slots__ = ('namespace', 'key', 'etag', 'created_utc', 'hit', 'value', 'body_bytes',
                 'body_sha256', '_body', '_gzip')

    def __init__(self, namespace, key, *, body=None, gzip=None, body_bytes=None, body_sha256=None,
                 created_utc=None, hit='built', value=None):
        if body is None and gzip is None:
            raise ValueError('An entry needs its body or its gzip.')
        if body is not None:
            body = bytes(body)
            body_bytes = len(body)
            if body_sha256 is None:
                body_sha256 = hashlib.sha256(body).hexdigest()
        self.namespace, self.key = namespace, key
        self.body_bytes, self.body_sha256 = body_bytes, body_sha256
        self.etag = '"' + body_sha256 + '"'
        self.created_utc = created_utc or _utc_now()
        self.hit, self.value = hit, value
        self._body, self._gzip = body, gzip

    @property
    def body(self):
        if self._body is None:
            body = _gzip.decompress(self._gzip)
            if len(body) != self.body_bytes:
                _forget(self.namespace, self.key)
                raise CorruptEntry('A cached response did not decompress to its recorded size; it will be rebuilt.')
            self._body = body
        return self._body

    @property
    def gzip(self):
        if self._gzip is None:
            self._gzip = _gzip.compress(self._body, compresslevel=GZIP_LEVEL, mtime=0)
        return self._gzip

    def shared(self, hit):
        """The same bytes for another caller, without this caller's object."""
        return Entry(self.namespace, self.key, body=self._body, gzip=self._gzip, body_bytes=self.body_bytes,
                     body_sha256=self.body_sha256, created_utc=self.created_utc, hit=hit)

    def __repr__(self):
        return f'<Entry {self.namespace}/{self.key[:12]} {self.hit} {self.body_bytes} bytes>'


class _Record:
    __slots__ = ('gzip', 'body_bytes', 'body_sha256', 'created_utc', 'touched', 'meta_path')

    def __init__(self, entry, meta_path):
        self.gzip, self.body_bytes, self.body_sha256 = entry.gzip, entry.body_bytes, entry.body_sha256
        self.created_utc, self.meta_path, self.touched = entry.created_utc, meta_path, time.monotonic()


class _Flight:
    __slots__ = ('done', 'entry')

    def __init__(self):
        self.done, self.entry = threading.Event(), None


def _count(namespace, counter, amount=1):
    with _mutex:
        table = _counters.setdefault(namespace, dict.fromkeys(_COUNTERS, 0))
        table[counter] += amount


def _problem(namespace, counter, message):
    _count(namespace, counter)
    _log.warning('response cache %s: %s', namespace, message)


_ledger_module = []


def _ledger():
    """access_stats when this process may use it for its disk tier (module docstring), else None."""
    if not _ledger_module:
        try:
            _ledger_module.append(importlib.import_module('access_stats'))
        except Exception:  # noqa: BLE001 - no ledger: eviction falls back to least recently used
            _ledger_module.append(None)
    ledger, root = _ledger_module[0], ROOT
    if ledger is None or root is None:
        return None
    try:
        live = os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(DEFAULT_ROOT))
        return ledger if live or not ledger.is_default() else None
    except Exception:  # noqa: BLE001
        return None


def _note(namespace, key, cost_s=None, size=None):
    ledger = _ledger()
    if ledger is not None:
        ledger.record('cache/' + namespace, key, cost_s=cost_s, size=size)


# ----------------------------------------------------------------------------- memory tier
def _trim_memory():
    global _memory_used
    with _mutex:
        while _memory and _memory_used > MEMORY_BYTES:
            (namespace, _key), record = _memory.popitem(last=False)
            _memory_used -= len(record.gzip)
            _count(namespace, 'memory_evictions')


def _memory_get(namespace, key):
    with _mutex:
        record = _memory.get((namespace, key))
        if record is None:
            return None
        _memory.move_to_end((namespace, key))
        touch = record.meta_path is not None and time.monotonic() - record.touched > TOUCH_SECONDS
        if touch:
            record.touched = time.monotonic()
    if touch:
        # Keep the disk LRU honest about an entry served from memory for hours.
        _touch(record.meta_path)
    return Entry(namespace, key, gzip=record.gzip, body_bytes=record.body_bytes, body_sha256=record.body_sha256,
                 created_utc=record.created_utc, hit='memory')


def _memory_put(namespace, key, entry, meta_path):
    global _memory_used
    record = _Record(entry, meta_path)
    if len(record.gzip) > MEMORY_BYTES:
        return
    with _mutex:
        old = _memory.pop((namespace, key), None)
        if old is not None:
            _memory_used -= len(old.gzip)
        _memory[(namespace, key)] = record
        _memory_used += len(record.gzip)
        _trim_memory()


def _forget(namespace, key):
    global _memory_used
    with _mutex:
        old = _memory.pop((namespace, key), None)
        if old is not None:
            _memory_used -= len(old.gzip)


def clear_memory(namespace=None):
    """Drop memory entries (all, or one namespace's); the disk tier is untouched."""
    global _memory_used
    with _mutex:
        for item in [item for item in _memory if namespace is None or item[0] == namespace]:
            _memory_used -= len(_memory.pop(item).gzip)


# ----------------------------------------------------------------------------- disk tier
def _folder(namespace):
    root = ROOT
    if root is None:
        return None
    folder = Path(root) / namespace
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _problem(namespace, 'disk_errors', f'no disk tier at {folder}: {error}')
        return None
    return folder


def _touch(path):
    try:
        os.utime(path, None)
    except OSError:
        pass


def _replace(source, target):
    # Windows refuses to replace a file another process has open; readers hold it briefly.
    for attempt in range(8):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(.02 * (attempt + 1))


def _write_atomic(target, data):
    temporary = target.with_name(f'{target.name}.{os.getpid()}-{threading.get_ident()}-{secrets.token_hex(4)}.tmp')
    try:
        with open(temporary, 'wb') as stream:
            stream.write(data)
        _replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _meta_valid(meta, namespace, key):
    try:
        return (isinstance(meta, dict) and meta.get('format') == FORMAT and meta.get('namespace') == namespace
                and meta.get('key') == key and isinstance(meta.get('gzip_bytes'), int)
                and isinstance(meta.get('body_bytes'), int) and meta['gzip_bytes'] >= 0 and meta['body_bytes'] >= 0
                and _KEY.fullmatch(str(meta.get('gzip_sha256'))) is not None
                and _KEY.fullmatch(str(meta.get('body_sha256'))) is not None
                and meta.get('etag') == '"' + meta['body_sha256'] + '"')
    except (TypeError, KeyError):
        return False


def _discard(folder, key, meta_bytes):
    """Remove an entry found corrupt: its meta only while it still holds the bytes that were
    judged (a rebuild may have replaced it since), and its body only when no meta and no
    build lock remain beside it."""
    meta, body = folder / (key + '.json'), folder / (key + '.gz')
    try:
        if meta_bytes is not None and meta.read_bytes() == meta_bytes:
            meta.unlink()
    except OSError:
        pass
    try:
        if not meta.exists() and not (folder / (key + '.lock')).exists():
            body.unlink()
    except OSError:
        pass


def _disk_get(folder, namespace, key, remember):
    meta_path = folder / (key + '.json')
    try:
        raw = meta_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return None  # being replaced right now; a miss, not corruption
    try:
        meta = json.loads(raw)
    except ValueError:
        meta = None
    if not _meta_valid(meta, namespace, key):
        _problem(namespace, 'corrupt', f'{key[:12]}: unreadable or foreign meta; rebuilding')
        _discard(folder, key, raw)
        return None
    try:
        data = (folder / (key + '.gz')).read_bytes()
    except FileNotFoundError:
        data = None
    except OSError:
        return None
    if data is None or len(data) != meta['gzip_bytes'] or hashlib.sha256(data).hexdigest() != meta['gzip_sha256']:
        _problem(namespace, 'corrupt', f'{key[:12]}: body missing, truncated or changed; rebuilding')
        _discard(folder, key, raw)
        return None
    entry = Entry(namespace, key, gzip=data, body_bytes=meta['body_bytes'], body_sha256=meta['body_sha256'],
                  created_utc=meta.get('created_utc'), hit='disk')
    try:
        entry.body  # noqa: B018 - decompress once: a body of the wrong size is never served
    except (CorruptEntry, OSError, EOFError, zlib.error, ValueError):
        _problem(namespace, 'corrupt', f'{key[:12]}: body does not decompress to its recorded size; rebuilding')
        _discard(folder, key, raw)
        return None
    _touch(meta_path)
    if remember:
        _memory_put(namespace, key, entry, meta_path)
    _count(namespace, 'disk_hits')
    return entry


def _disk_put(folder, namespace, key, entry, key_parts, deps):
    data = entry.gzip
    meta = {'format': FORMAT, 'namespace': namespace, 'key': key, 'etag': entry.etag,
            'body_bytes': entry.body_bytes, 'body_sha256': entry.body_sha256,
            'gzip_bytes': len(data), 'gzip_sha256': hashlib.sha256(data).hexdigest(), 'gzip_level': GZIP_LEVEL,
            'created_utc': entry.created_utc, 'pid': os.getpid(), 'key_parts': key_parts, 'deps': deps}
    try:
        _write_atomic(folder / (key + '.gz'), data)
        _write_atomic(folder / (key + '.json'),
                      json.dumps(meta, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    except OSError as error:
        _problem(namespace, 'disk_errors', f'could not store {key[:12]}: {error}')
        return False
    _count(namespace, 'disk_writes')
    try:
        _enforce_cap(folder, namespace, key)
    except OSError as error:
        _problem(namespace, 'disk_errors', f'could not enforce the disk cap: {error}')
    return True


def _scan(folder):
    """{key: {'size', 'used', 'meta', 'body', 'mtime'}} plus stale temporary files."""
    found, stale = {}, []
    now = time.time()
    with os.scandir(folder) as items:
        for item in items:
            name = item.name
            try:
                info = item.stat()
            except OSError:
                continue
            if name.endswith('.tmp'):
                if now - info.st_mtime > ORPHAN_SECONDS:
                    stale.append(item.path)
                continue
            stem, _, suffix = name.partition('.')
            if suffix not in ('json', 'gz') or not _KEY.fullmatch(stem):
                continue
            record = found.setdefault(stem, {'size': 0, 'used': 0.0, 'used_ns': 0, 'meta': False, 'body': False,
                                             'mtime': 0.0})
            record['size'] += info.st_size
            record['mtime'] = max(record['mtime'], info.st_mtime)
            if suffix == 'json':
                record['meta'], record['used'], record['used_ns'] = True, info.st_mtime, info.st_mtime_ns
            else:
                record['body'] = True
    return found, stale


def _remove_entry(folder, key):
    removed = True
    for suffix in ('.json', '.gz'):
        try:
            (folder / (key + suffix)).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            removed = False  # open in another process right now; left for a later pass
    return removed


def _eviction_order(found, scores, now):
    """_scan() entries, the first to evict first (module docstring, Eviction by value): warm entries
    no request has used, then the lowest score, then least recently used; recently used ones last."""
    def rank(item):
        key, record = item
        if record['meta'] and WARM_MTIME_NS[0] <= record['used_ns'] < WARM_MTIME_NS[1]:
            return 0, 0.0, record['used'], key
        return (2 if now - record['used'] < FRESH_SECONDS else 1), scores.get(key, 0.0), record['used'], key
    return sorted(found.items(), key=rank)


def _enforce_cap(folder, namespace, keep):
    limit = DISK_LIMITS.get(namespace, DISK_BYTES)
    found, stale = _scan(folder)
    now = time.time()
    for path in stale:
        try:
            os.unlink(path)
        except OSError:
            pass
    for key, record in list(found.items()):
        # Half an entry (a process died between its two writes) is never served; clear it once
        # it is clearly abandoned rather than mid-write.
        if key != keep and not (record['meta'] and record['body']) and now - record['mtime'] > ORPHAN_SECONDS:
            if not (folder / (key + '.lock')).exists() and _remove_entry(folder, key):
                del found[key]
    total = sum(record['size'] for record in found.values())
    if total <= limit:
        return
    ledger = _ledger()
    for key, record in _eviction_order(found, ledger.scores('cache/' + namespace) if ledger is not None else {}, now):
        if key == keep:
            continue
        # Only the disk copy goes: a memory copy is the same verified bytes and stays valid.
        if _remove_entry(folder, key):
            total -= record['size']
            _count(namespace, 'disk_evictions')
        if total <= limit:
            break


def disk_usage(namespace):
    """{'entries', 'bytes', 'limit'} of one namespace's disk tier, or None when it is off."""
    folder = None if ROOT is None else Path(ROOT) / namespace
    if folder is None:
        return None
    try:
        found, _ = _scan(folder)
    except FileNotFoundError:
        found = {}
    return {'entries': sum(1 for record in found.values() if record['meta'] and record['body']),
            'bytes': sum(record['size'] for record in found.values()),
            'limit': DISK_LIMITS.get(namespace, DISK_BYTES)}


# ----------------------------------------------------------------------------- cross-process locks
def _inspect(path):
    """None when there is no lock at `path`; otherwise (live, bytes read or None), by proclock's rule."""
    found = proclock.inspect(path)
    return None if found is None else (found.live, found.raw)


def _unlink(path, expected):
    """Remove the lock at `path` only while it still holds `expected` (None: only while it is still an
    abandoned unreadable body)."""
    proclock.release(path, expected)


def _claim(path):
    """Create the lock at `path` (O_CREAT|O_EXCL) holding {pid, pid_started, started_utc, key}. Its
    bytes, or None while another live process holds it. A stale lock is removed and the claim retried."""
    return proclock.claim(path, path.stem)


def _acquire(lock, folder, namespace, key, remember, deadline):
    """(lock bytes, None) once this process may build `key`, or (None, entry) when another
    process stored it meanwhile. Waits while a live process holds the lock."""
    waited, delay = False, .02
    while True:
        claimed = _claim(lock)
        if claimed is not None:
            entry = _disk_get(folder, namespace, key, remember)  # stored just before the claim
            if entry is not None:
                _unlink(lock, claimed)
                return None, entry
            return claimed, None
        if not waited:
            waited = True
            _count(namespace, 'waits')
        if time.monotonic() >= deadline:
            raise StillBuilding(STILL_BUILDING)
        time.sleep(delay)
        delay = min(delay * 1.5, .25)
        entry = _disk_get(folder, namespace, key, remember)
        if entry is not None:
            return None, entry


# ----------------------------------------------------------------------------- get_or_compute
def lookup(namespace, key_parts, deps, *, memory=True, disk=True):
    """The stored entry for this key (memory, then disk) or None. Never computes or waits."""
    key = entry_key(namespace, key_parts, deps)
    if memory:
        entry = _memory_get(namespace, key)
        if entry is not None:
            _count(namespace, 'memory_hits')
            return entry
    folder = _folder(namespace) if disk and ROOT is not None else None
    return _disk_get(folder, namespace, key, memory) if folder is not None else None


def get_or_compute(namespace, key_parts, deps, compute, *, encode=None, persist=None, memory=True, disk=True,
                   wait_seconds=None):
    """The answer for (namespace, key_parts, deps): from memory, else from disk, else built by
    compute() exactly once across this process's threads and every process sharing the root.

    compute() returns the body (bytes) or a value that encode (default json_body) turns into
    it. persist(value) False answers this call without storing the entry (a build that caught
    a possibly passing failure). memory/disk False skip a tier. An exception from compute()
    propagates and stores nothing."""
    key = entry_key(namespace, key_parts, deps)
    if memory:
        entry = _memory_get(namespace, key)
        if entry is not None:
            _count(namespace, 'memory_hits')
            _note(namespace, key)
            return entry
    deadline = time.monotonic() + (WAIT_SECONDS if wait_seconds is None else wait_seconds)
    while True:
        with _mutex:
            flight = _flights.get(key)
            leader = flight is None
            if leader:
                flight = _flights[key] = _Flight()
        if leader:
            break
        if not flight.done.wait(max(0.0, deadline - time.monotonic())):
            raise StillBuilding(STILL_BUILDING)
        if flight.entry is not None:
            _count(namespace, 'memory_hits')
            _note(namespace, key)
            return flight.entry.shared('memory')
        if memory:
            entry = _memory_get(namespace, key)
            if entry is not None:
                _count(namespace, 'memory_hits')
                _note(namespace, key)
                return entry
        # The build this thread waited for failed: this thread may build it now.
    try:
        entry = _lead(namespace, key, key_parts, deps, compute, encode, persist, memory, disk, deadline)
        flight.entry = entry
        return entry
    finally:
        with _mutex:
            _flights.pop(key, None)
        flight.done.set()


def _lead(namespace, key, key_parts, deps, compute, encode, persist, memory, disk, deadline):
    folder = _folder(namespace) if disk else None
    lock = claimed = None
    if folder is not None:
        try:
            entry = _disk_get(folder, namespace, key, memory)
            if entry is not None:
                _note(namespace, key)
                return entry
            lock = folder / (key + '.lock')
            claimed, entry = _acquire(lock, folder, namespace, key, memory, deadline)
            if entry is not None:
                _note(namespace, key)
                return entry
        except OSError as error:
            _problem(namespace, 'disk_errors', f'disk tier skipped for {key[:12]}: {error}')
            folder = lock = claimed = None
    try:
        started = time.perf_counter()
        value = compute()
        if isinstance(value, (bytes, bytearray, memoryview)):
            body = bytes(value)
        else:
            body = (encode or json_body)(value)
        entry = Entry(namespace, key, body=body, value=value, hit='built')
        if persist is None or persist(value):
            if memory:
                _memory_put(namespace, key, entry, folder / (key + '.json') if folder is not None else None)
            if folder is not None:
                # The build's seconds and stored size (the gzip _disk_put writes) value it (access_stats).
                _note(namespace, key, cost_s=time.perf_counter() - started, size=len(entry.gzip))
                _disk_put(folder, namespace, key, entry, key_parts, deps)
        else:
            _count(namespace, 'kept_out')
        _count(namespace, 'builds')
        return entry
    finally:
        if claimed is not None:
            _unlink(lock, claimed)


# ----------------------------------------------------------------------------- code and file identity
def _file_state(path):
    """(resolved path, size, mtime_ns, ctime_ns) or None when the file is missing."""
    try:
        resolved = Path(path).resolve()
        info = resolved.stat()
    except FileNotFoundError:
        return None
    return str(resolved), info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _stat_state(resolved):
    """_file_state() of a path that is already resolved, without resolving it again (the per-call
    re-check of a code identity's files)."""
    try:
        info = os.stat(resolved)
    except FileNotFoundError:
        return None
    return resolved, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def file_sha256(path):
    """Content SHA-256 of a file, memoized per (path, size, mtime_ns, ctime_ns). None when the
    file is missing or changed while it was read."""
    state = _file_state(path)
    if state is None:
        return None
    with _mutex:
        known = _digests.get(state)
        if known is not None:
            _digests.move_to_end(state)
            return known
    with open(state[0], 'rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if _file_state(path) != state:
        return None
    with _mutex:
        _digests[state] = digest
        while len(_digests) > 256:
            _digests.popitem(last=False)
    return digest


def process_started_ns():
    """When this process was created (ns since the epoch). Windows: GetProcessTimes (proclock);
    elsewhere the import of this module, which is later and therefore a weaker bound."""
    global _started_ns
    if _started_ns is None:
        created = None
        if os.name == 'nt':
            try:
                ticks = proclock.own_started()
                created = (ticks - proclock.EPOCH_TICKS) * 100 if ticks else None
            except (OSError, AttributeError, ValueError):
                created = None
        _started_ns = created or _IMPORTED_NS
    return _started_ns


def _source_of(name, folders=()):
    """The source file of module `name`: the loaded module's, else `<folder>/<name>.py` in the
    first of `folders` that has one (the application folders share no module name), else what
    the import system would find on sys.path now."""
    module = sys.modules.get(name)
    path = getattr(module, '__file__', None) if module is not None else None
    if path is None:
        for folder in folders:
            candidate = Path(folder) / f'{name}.py'
            if candidate.is_file():
                path = str(candidate)
                break
    if path is None:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            return None
        path = spec.origin if spec is not None and spec.has_location else None
    if not path or not str(path).endswith('.py'):
        return None
    return str(Path(path).resolve())


def _imports(path):
    """Top-level names a source file imports, anywhere in it; memoized per file state, so the
    closures of several root modules share one parse of each file."""
    state = _file_state(path)
    with _mutex:
        known = _parsed.get(state)
    if known is not None:
        return known
    tree = ast.parse(Path(path).read_bytes(), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split('.')[0])
    names = frozenset(names)
    if state is not None and _file_state(path) == state:
        with _mutex:
            _parsed[state] = names
    return names


def module_closure(root_module):
    """{module name: source file} for `root_module` and every module it imports, directly or
    not, anywhere in its source (function-level imports included), whose source lives beside
    it, in the tools folder beside that (mentat/tools: contribution_data imports three of its
    modules as it loads) or beside this file. The standard library and site-packages are not
    followed."""
    first = _source_of(root_module)
    if first is None:
        raise ModuleNotFoundError(f'No source file for {root_module}.')
    folders = (Path(first).parent, Path(first).parent.parent / 'tools', Path(__file__).resolve().parent)
    homes = {os.path.normcase(str(folder)) for folder in folders}
    found, pending = {}, [root_module, __name__]
    while pending:
        name = pending.pop()
        if name in found:
            continue
        # Resolved in the application folders too, so the closure does not depend on whether
        # something has already put mentat/tools on sys.path in this process.
        path = _source_of(name, folders)
        if path is None or os.path.normcase(str(Path(path).parent)) not in homes:
            continue
        found[name] = path
        pending.extend(sorted(_imports(path) - set(found)))
    return found


class _Code:
    __slots__ = ('digest', 'files', 'reason')

    def __init__(self, digest, files, reason=None):
        self.digest, self.files, self.reason = digest, files, reason


def _code_of(root_module):
    started = process_started_ns()
    hashes, files = {}, {}
    for name, path in sorted(module_closure(root_module).items()):
        state = _file_state(path)
        if state is None:
            return _Code(None, {}, f'{name}: its source file is missing')
        if state[2] >= started or state[3] >= started:
            return _Code(None, {}, f'{Path(path).name} changed after this process started')
        digest = file_sha256(path)  # memoized per file state: shared by every root module's closure
        if digest is None or _file_state(path) != state:
            return _Code(None, {}, f'{Path(path).name} changed while it was read')
        hashes[name] = digest
        files[path] = state
    digest = hashlib.sha256(canonical({'modules': hashes, 'python': sys.version, 'format': FORMAT}).encode('ascii'))
    return _Code(digest.hexdigest(), files)


def code_identity(root_module, extra=None):
    """Identity of the code that computes an answer: `root_module` and every module of this
    application it imports (module_closure), by content, with the Python version and `extra`
    (JSON: whatever else decides the answer, e.g. a native helper's hash).

    None when the running code may differ from those files: one of them was written after
    this process started (it may have been loaded before the write) or has changed since it
    was hashed. A None disables the disk tier for that answer in this process - the memory
    tier, which only ever serves this process's own code, is unaffected."""
    with _code_lock:
        code = _code.get(root_module)
        if code is None:
            try:
                code = _code_of(root_module)
            except (OSError, SyntaxError, ValueError, ModuleNotFoundError) as error:
                code = _Code(None, {}, f'{type(error).__name__}: {error}')
            _code[root_module] = code
            if code.digest is None:
                _log.warning('response cache: disk tier off for %s in this process: %s', root_module, code.reason)
        if code.digest is not None:
            for path, recorded in code.files.items():
                if _stat_state(path) != recorded:
                    code.digest, code.reason = None, f'{Path(path).name} changed while this process runs'
                    _log.warning('response cache: disk tier off for %s in this process: %s', root_module, code.reason)
                    break
        digest = code.digest
    if digest is None:
        return None
    return hashlib.sha256(canonical({'code': digest, 'extra': extra}).encode('ascii')).hexdigest()


# ----------------------------------------------------------------------------- HTTP
def accepts_gzip(header):
    """server.py accepts_gzip(), the same rule."""
    for part in (header or '').split(','):
        parts = [piece.strip() for piece in part.lower().split(';')]
        if parts[0] != 'gzip':
            continue
        try:
            return all(float(piece[2:]) > 0 for piece in parts[1:] if piece.startswith('q='))
        except ValueError:
            return False
    return False


def etag(entry, gzipped=False):
    """The strong ETag of an entry's body; the gzip representation carries '-gzip' inside the
    quotes, since two content-codings are two representations (RFC 9110 8.8.3)."""
    return entry.etag[:-1] + '-gzip"' if gzipped else entry.etag


def not_modified(if_none_match, entry):
    """Whether an If-None-Match header matches this entry (weak comparison, as RFC 9110 13.1.2
    requires; either representation's tag names the same body)."""
    if not if_none_match:
        return False
    tags = {entry.etag, etag(entry, True)}
    for tag in str(if_none_match).split(','):
        tag = tag.strip()
        if tag == '*':
            return True
        if tag.startswith('W/'):
            tag = tag[2:]
        if tag in tags:
            return True
    return False


def respond(entry, if_none_match=None, accept_encoding=None, *, gzip_ok=None, content_type='application/json',
            cache_control='private, no-cache'):
    """(status, [(header, value)], body) for an entry: 304 with no body when If-None-Match
    matches; otherwise 200 with the stored gzip when the client accepts it (same 2048-byte
    floor as Handler.send), else the plain body."""
    compressed = (accepts_gzip(accept_encoding) if gzip_ok is None else bool(gzip_ok)) and entry.body_bytes >= GZIP_MIN_BYTES
    tag = etag(entry, compressed)
    if not_modified(if_none_match, entry):
        return 304, [('Content-Type', content_type), ('Cache-Control', cache_control),
                     ('Vary', 'Accept-Encoding'), ('ETag', tag)], b''
    body = entry.gzip if compressed else entry.body
    headers = [('Content-Type', content_type), ('Content-Length', str(len(body))),
               ('Cache-Control', cache_control), ('Vary', 'Accept-Encoding')]
    if compressed:
        headers.append(('Content-Encoding', 'gzip'))
    headers.append(('ETag', tag))
    return 200, headers, body


def from_value(value, *, encode=None, namespace='value'):
    """An uncached entry for a value computed per request (its ETag still lets an unchanged
    answer revalidate with a 304; its gzip is made only if it is sent)."""
    body = (encode or json_body)(value)
    digest = hashlib.sha256(body).hexdigest()
    return Entry(namespace, digest, body=body, body_sha256=digest, hit='built', value=value)


# ----------------------------------------------------------------------------- stats
def stats(namespace=None):
    """Counters and footprint per namespace, for prewarm status and bench."""
    with _mutex:
        names = sorted(set(_counters) | {item[0] for item in _memory})
        memory_by = {}
        for (name, _key), record in _memory.items():
            used = memory_by.setdefault(name, [0, 0])
            used[0] += 1
            used[1] += len(record.gzip)
        counters = {name: dict(_counters.get(name) or dict.fromkeys(_COUNTERS, 0)) for name in names}
        result = {'root': str(ROOT) if ROOT is not None else None,
                  'memory': {'entries': len(_memory), 'bytes': _memory_used, 'limit': MEMORY_BYTES},
                  'namespaces': {}}
    for name in ([namespace] if namespace else names):
        row = counters.get(name) or dict.fromkeys(_COUNTERS, 0)
        row['memory_entries'], row['memory_bytes'] = memory_by.get(name, [0, 0])
        try:
            row['disk'] = disk_usage(name)
        except OSError as error:
            row['disk'] = {'error': str(error)}
        result['namespaces'][name] = row
    return result
