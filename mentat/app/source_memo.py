"""Validated, single-flight memo of contribution_data._sources() (perf H6).

Every Insights poll, contributions catalog and session request rediscovers every
published snapshot: _sources() globs the report directory, stats and hashes each
Overview workbook and any companion Data book, and reads each industry's payload
manifest and the first 2 MB of its payload, plus a recovered source's hydration
receipt and database. That costs 40-50 ms a call and an Insights poll pays it twice.

install() rebinds contribution_data._sources, a module global, so catalog() and
_source() inside contribution_data use the memo too. sources() answers a deep copy
of the last result while EVERY input _sources() consults is unchanged:

  * the report directory's own stat (an entry added, removed or renamed) and the
    names its '* Industry Spending Overview.xlsx' glob returns;
  * per workbook: where it resolves and its stat, and its companion
    '... Industry Spending Data.xlsx' (present or absent) with where that resolves;
  * the payload manifest and the payload named by the workbook's pinned source hash;
  * the database path the payload names: its stat, and where it resolves when it exists;
  * for a recovered source: the hydration receipt, where its output resolves, that
    file's stat, and where the project's .cache folder resolves.

A stat is (type, size, mtime_ns, ctime_ns, st_ino, st_dev), 'missing', or the error,
so an edit, a replacement (a new file id), a deletion or a new file each invalidate.
The inputs are found by taking the same steps _sources() takes, and a result is kept
only when every input reads the same after the unmemoized call as before it. A step
that met an I/O failure other than a missing file (a locked workbook, a payload the
process cannot read) is never kept, so a transient failure cannot outlive its cause.
Nothing is persisted. The content hashes _sources() reports still come from
contribution_data's full-content SHA-256, keyed by each file's stat exactly as before.

The ledger is not memoized: _sources() never reads it. contribution_data._token()
resolves it on each copy, and a copy never carries 'ledger', so a new ledger report
is seen on the next call exactly as without this module.

Copies: callers mutate the dicts (_token() adds 'ledger'), so every call gets its own
deep copy. Single flight (DE-026): one discovery at a time under an explicit lock;
callers arriving while it runs wait, then share its result.
"""
from collections import OrderedDict
import copy
import os
from pathlib import Path
import stat as _mode
import threading
import zipfile

import contribution_data as data
import report_publication
from receipt_path import receipt_path
from report_identity import identity, companion_name, lineage_paths

PATTERN = '* Industry Spending Overview*.xlsx'
LIMIT = 8  # kept entries: report directory x project root x the helpers in use

_lock = threading.RLock()
_entries = OrderedDict()
_original = data._sources  # the unmemoized discovery, captured before install()


def install():
    """Serve contribution_data._sources from the memo. Idempotent."""
    with _lock:
        if data._sources is not sources:
            data._sources = sources
    return sources


def uninstall():
    """Restore the unmemoized contribution_data._sources and drop every entry."""
    with _lock:
        if data._sources is sources:
            data._sources = _original
        _entries.clear()


def _stat(path):
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return ('missing',)
    except (OSError, ValueError) as error:
        return ('error', type(error).__name__, getattr(error, 'winerror', None), getattr(error, 'errno', None))
    kind = 'file' if _mode.S_ISREG(info.st_mode) else 'dir' if _mode.S_ISDIR(info.st_mode) else 'other'
    return (kind, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_dev)


def _real(path):
    # Path.resolve() is os.path.realpath() in this interpreter.
    try:
        return os.path.realpath(path)
    except (OSError, ValueError) as error:
        return ('error', type(error).__name__)


def _names(root):
    try:
        return tuple(path.name for path in sorted(Path(root).glob(PATTERN)))
    except OSError as error:
        return ('error', type(error).__name__)


_READ = {'stat': _stat, 'real': _real, 'glob': _names}


def _evaluate(spec):
    return [_READ[kind](argument) for kind, argument in spec]


def _discover(directory):
    """(spec, values, keep): every input _sources(directory) consults, read now, in
    _sources()'s own order and by its own steps. keep is False when a step failed in a
    way the recorded inputs could not explain (anything but a missing file)."""
    spec, values = [], []
    keep = True

    def watch(kind, argument):
        value = _READ[kind](argument)
        spec.append((kind, argument))
        values.append(value)
        return value

    def explained(error):
        # A missing file is recorded as 'missing' and re-checked; any other I/O
        # failure may pass with nothing recorded changing, so it is never kept.
        return isinstance(error, FileNotFoundError)

    try:
        root = Path(directory).resolve()
        if watch('stat', str(root))[0] != 'dir':
            return spec, values, False  # _sources() raises; nothing to keep
        names = watch('glob', str(root))
        if names[:1] == ('error',):
            return spec, values, False
        cache_root = None
        for name in names:
            info = identity(name)
            if name.startswith('~$') or info is None or info['kind'] != 'overview':
                continue
            workbook = root / name
            industry = info['industry']
            if not Path(watch('real', str(workbook))).is_relative_to(root):
                return spec, values, False  # _sources() raises for this report
            watch('stat', str(workbook))
            for dependency in report_publication.dependencies(workbook):
                watch('stat', str(dependency))
                watch('real', str(dependency))
            try:
                data._hash(workbook)
            except OSError as error:
                keep = keep and explained(error)
                continue
            sibling = workbook.with_name(companion_name(info))
            if watch('stat', str(sibling))[0] == 'file':
                if Path(watch('real', str(sibling))).is_relative_to(root):
                    try:
                        data._hash(sibling)
                    except OSError as error:
                        keep = keep and explained(error)
                    try:
                        data._workbook_source(data._stamp(sibling))
                        data._workbook_source(data._stamp(workbook))
                    except OSError as error:
                        keep = keep and explained(error)
                    except (ValueError, KeyError, zipfile.BadZipFile):
                        pass
            try:
                db_hash = data._workbook_source(data._stamp(workbook))
                cache = data._report_cache_root() / data._slug(industry)
                manifest_path, payload = lineage_paths(cache, db_hash, info['cycle_label'])
                watch('stat', str(manifest_path))
                if data._json(manifest_path).get('database_sha256') != db_hash:
                    continue
                watch('stat', str(payload))
                database = str(receipt_path(data._payload_metadata(payload)['database_path']))
                # Path(database).resolve().is_file(): stat() follows the same links. Only
                # an existing database's resolved path is reported, so only then resolve.
                if watch('stat', database)[0] == 'file':
                    watch('real', database)
                    continue
                hydration_path = (data._data_cache_root() / 'receipt-metadata-repair-20260910' / data._slug(industry)
                                  / 'mentat.db.hydration.json')
                watch('stat', str(hydration_path))
                hydration = data._json(hydration_path)
                if hydration.get('source_sha256') != db_hash:
                    continue
                output = watch('real', str(receipt_path(hydration['output'])))
                if cache_root is None:
                    cache_root = watch('real', str(data._data_cache_root()))
                if not Path(output).is_relative_to(cache_root) or watch('stat', output)[0] != 'file':
                    continue
                data._hash(Path(output))
            except OSError as error:
                keep = keep and explained(error)
            except (ValueError, KeyError, zipfile.BadZipFile):
                pass
    except Exception:  # noqa: BLE001 - _sources() itself raises here; never keep
        return spec, values, False
    return spec, values, keep


def _key(directory):
    return (str(Path(directory).resolve()), str(data.PROJECT),
            str(data._report_cache_root()), str(data._data_cache_root()), _original, data._hash, data._stamp,
            data._json, data._payload_metadata, data._workbook_source, data._slug)


def _fresh(entry):
    return entry is not None and _evaluate(entry[0]) == entry[1]


def sources(directory):
    """contribution_data._sources(directory), from the memo while every input holds."""
    try:
        key = _key(directory)
    except (OSError, TypeError, ValueError):
        return _original(directory)
    entry = _entries.get(key)
    if _fresh(entry):
        return copy.deepcopy(entry[2])
    with _lock:
        entry = _entries.get(key)
        if _fresh(entry):
            return copy.deepcopy(entry[2])
        spec, values, keep = _discover(directory)
        result = _original(directory)
        if keep and _evaluate(spec) == values:
            _entries[key] = (spec, values, copy.deepcopy(result))
            _entries.move_to_end(key)
            while len(_entries) > LIMIT:
                _entries.popitem(last=False)
        else:
            _entries.pop(key, None)
        return result
