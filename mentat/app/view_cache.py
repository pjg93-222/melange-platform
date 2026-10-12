"""Bounded, single-flight cache for read-only, dependency-keyed UI payloads.

The memory tier is the one it always was: encoded views keyed by the caller's key, LRU by
entries and bytes, one build per key, each caller decoding its own copy.

Behind it, views built by a module named in DISK_TIERS (Graph views: graph_data) are also
kept on disk through response_cache once the process has called response_cache.configure(),
so an encoded view survives a restart. The disk key is the caller's key exactly (for Graph:
paths, the file signature of every dependency, the filters) plus the SHA-256 of the content
of every file in that signature and the identity of the code that builds the view
(response_cache.code_identity: graph_data and each application module it imports, the
Python version, the native scanner it would run and the openpyxl it has loaded). A file
whose content changed, or changed code, is therefore another key. Before a disk entry is
used or written, every file in the key's signature must still carry the stamp it was
hashed under; otherwise that request stays memory-only, as before.
"""
from collections import OrderedDict
import json
import logging
from pathlib import Path
import sys
import threading
import time

import response_cache

_log = logging.getLogger('mentat.cache')
_DECODE = object()


class FileSignature(tuple):
    """file_signature()'s result: a plain tuple (hash and equality are tuple's) of
    (path, mtime_ns, ctime_ns, size) entries, marked so the disk tier can find the files a
    key depends on and verify their content."""
    __slots__ = ()


def file_signature(paths):
    result = []
    for path in sorted(set(map(Path, paths)), key=str):
        try:
            stat = path.stat()
            result.append((str(path.resolve()), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
        except FileNotFoundError:
            result.append((str(path.absolute()), None, None, None))
    return FileSignature(result)


def graph_code_extra():
    """What decides a Graph answer besides the Python modules: the native workbook scanner
    this process would run now (native_scan.executable(): none, or its path and content
    hash) and the openpyxl it has loaded (the reference reader)."""
    import native_scan
    binary = native_scan.executable()
    native = None
    if binary is not None:
        native = {'path': str(binary), 'sha256': response_cache.file_sha256(binary)}
        if native['sha256'] is None:
            raise OSError('The native scanner changed while it was hashed.')
    openpyxl = sys.modules.get('openpyxl')
    return {'native': native, 'openpyxl': getattr(openpyxl, '__version__', None)}


def graph_view_persistable(value):
    """Whether a Graph view may be written to disk. A view whose downstream-activity read
    failed carries candidate_available False and the error as its note (graph_data
    _graph_view); the error can be a passing one (a locked file), so that view is kept in
    memory as before but never on disk, where it would outlive the process."""
    return not (isinstance(value, dict) and value.get('candidate_available') is False)


# Builder module -> (response_cache namespace, persist(value), extra code identity).
DISK_TIERS = {'graph_data': ('graph-views', graph_view_persistable, graph_code_extra)}


def _encode(result):
    return json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def _signatures(value):
    if isinstance(value, FileSignature):
        yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _signatures(item)


def _plain(value):
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f'{type(value).__name__} in a view key')


def _disk_fetch(key, build):
    """A zero-argument function answering `key` through the disk tier, or None when this
    request stays memory-only (no disk tier, a builder not in DISK_TIERS, code that changed
    under this process, or a dependency that moved while it was hashed)."""
    if not response_cache.enabled():
        return None
    module = getattr(build, '__module__', None)
    tier = DISK_TIERS.get(module)
    if tier is None:
        return None
    namespace, persist, extra = tier
    try:
        code = response_cache.code_identity(module, extra=extra())
        if code is None:
            return None
        signatures = list(_signatures(key))
        # report_insights.digest_stamp is the builder's own memo of the same hash (report_facts
        # records it as each report's sha256), so a file is hashed once per stamp per process.
        from report_insights import digest_stamp
        content = [[None if entry[1] is None else digest_stamp(tuple(entry)) for entry in signature]
                   for signature in signatures]
        if any(file_signature(entry[0] for entry in signature) != signature for signature in signatures):
            return None
        key_parts = _plain(key)
    except (OSError, ValueError, TypeError, ImportError) as error:
        _log.warning('view cache: disk tier skipped for one %s view: %s', module, error)
        return None
    deps = {'content_sha256': content, 'code': code}
    return lambda: response_cache.get_or_compute(namespace, key_parts, deps, build, encode=_encode,
                                                 persist=persist, memory=False)


class ViewCache:
    def __init__(self, entries=32, max_bytes=64 * 1024 * 1024):
        self.entries, self.max_bytes = entries, max_bytes
        self.values, self.pending = OrderedDict(), set()
        self.condition = threading.Condition()
        self.bytes = self.hits = self.misses = 0
        self.disk_hits = self.disk_builds = 0

    def get(self, key, build):
        deadline = time.monotonic() + 120
        with self.condition:
            while key in self.pending:
                left = deadline - time.monotonic()
                if left <= 0 or not self.condition.wait(left):
                    raise ValueError('This view is still being prepared. Retry shortly.')
            if key in self.values:
                self.hits += 1
                encoded = self.values[key]
                self.values.move_to_end(key)
                # Each caller owns its object; server provenance annotations
                # and client-specific changes cannot poison the shared result.
                return json.loads(encoded)
            self.pending.add(key)
            self.misses += 1
        try:
            fetch = _disk_fetch(key, build)
            if fetch is None:
                result = build()
                encoded = _encode(result)
            else:
                entry = fetch()
                encoded = entry.body
                result = entry.value if entry.hit == 'built' else _DECODE
            with self.condition:
                if fetch is not None:
                    if entry.hit == 'disk':
                        self.disk_hits += 1
                    elif entry.hit == 'built':
                        self.disk_builds += 1
                if len(encoded) <= self.max_bytes:
                    while self.values and (len(self.values) >= self.entries or self.bytes + len(encoded) > self.max_bytes):
                        self.bytes -= len(self.values.popitem(last=False)[1])
                    self.values[key] = encoded
                    self.bytes += len(encoded)
            # A view read from disk is decoded like a memory hit: the caller owns its object.
            return json.loads(encoded) if result is _DECODE else result
        finally:
            with self.condition:
                self.pending.discard(key)
                self.condition.notify_all()

    def clear(self):
        with self.condition:
            self.values.clear()
            self.bytes = self.hits = self.misses = 0
            self.disk_hits = self.disk_builds = 0

    def info(self):
        with self.condition:
            return {'entries':len(self.values), 'bytes':self.bytes, 'hits':self.hits,
                    'misses':self.misses, 'in_progress':len(self.pending),
                    'disk_hits':self.disk_hits, 'disk_builds':self.disk_builds}
