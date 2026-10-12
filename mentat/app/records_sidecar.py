"""Records sidecar: an analytical index's observations in record-inspection order.

/api/contributions/records pages source observations ordered by (date DESC, source_id). The analytical
index has no index on date, so every page sorted the whole facts table and counted it again: on Banking
(708 MB) one request made two full scans, 81.9 s cold and 4.8 s warm (peer finding 2, 2026-09-24).

A sidecar is a separate SQLite file per index stamp. Its table `ordered` holds the index's observations
already in that order (record_rank 1..n is the position), with every column a records filter can name
except the four long location notes, and stored counts for the unfiltered selections. It answers which
source IDs fall on a page and how many observations match; the page's rows themselves are still read
from the analytical index, by source ID, with the records query's own SQL. The analytical index file is
never opened for writing: its stamp feeds cache keys.

A sidecar is published only after it is proven: every copied value is the index's value for the same
source ID (same storage class, compared with IS), the counts agree, source IDs are unique and present,
consecutive ranks are strictly in (date DESC, source_id) order, and PRAGMA integrity_check passes. Its
proof records the full sha256 of the file, which a process verifies before its first use.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

import contribution_data as data
import contribution_query_store as store
import contribution_semantics as semantics

LOADED_STAMP = data._stamp(__file__)
LOADED_SHA = data._hash(__file__)
# Long free-text location notes: no records filter is expected on them. A filter that names one is
# answered by the records query's own full path (covers()).
EXCLUDED = ('recipient_location_status', 'represented_location_status',
            'recipient_location_evidence', 'represented_location_evidence')
KEYS = ('source_id',) + tuple(key for key in data.DIMENSIONS if key not in EXCLUDED)
MONEY = ('raw_cents', 'receipt_cents', 'original_cents', 'supported_cents', 'pac_cents', 'unresolved')
COLUMNS = KEYS + MONEY
# Secondary indexes: the analytical index's own filter indexes, one column each so that equal values
# stay in rank order (SQLite appends the rowid, which is record_rank).
INDEXED = ('company', 'committee_id', 'zip5', 'state')
# An index smaller than this is paged by the records query's full path (a few milliseconds there).
MIN_INDEX_BYTES = 32 * 1024 * 1024
BATCH_ROWS = 50000
_mutex = threading.Lock()
_verified = {}   # sidecar path -> ((stamp, header), proof stamp) proven in this process
_totals = {}     # sidecar path -> (the same signature, stored totals)


def check():
    """Raise when this file changed after it was loaded; contribution_query._check_loaded calls it (2026-09-25)."""
    if data._stamp(__file__) != LOADED_STAMP:
        raise ValueError('Contribution query code changed. Restart the app to load the current query rules.')


def root():
    """Where sidecars live: <query cache root>/records (store.ROOT is read at call time)."""
    return Path(store.ROOT) / 'records'


def identity(index_path):
    return {'index': list(data._stamp(index_path)), 'code': LOADED_SHA}


def key(ident):
    return hashlib.sha256(json.dumps(ident, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def location(ident):
    """(sidecar, proof, lock, progress) paths for one identity."""
    name = key(ident)
    base = root() / name
    return (base.with_suffix('.sqlite'), base.with_suffix('.json'), base.with_suffix('.lock'),
            base.with_suffix('.progress'))


def eligible(index_path):
    """True when records for this index are paged through a sidecar."""
    try:
        return Path(index_path).stat().st_size >= MIN_INDEX_BYTES
    except OSError:
        return False


def covers(filters, column):
    """True when every filter names a field the sidecar holds (a derived field is computed from them)."""
    fields = set()
    for item in filters:
        if not isinstance(item, dict):
            return False
        field = item.get('field')
        fields.add(column if field in ('amount', 'amount_cents') else field)
    return fields <= set(COLUMNS) | set(semantics.DERIVED)


def _signature(path):
    with Path(path).open('rb') as stream:
        header = stream.read(100)
    return data._stamp(path), header


def _file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def valid(path, proof_path, ident):
    """True when the sidecar at `path` is the proven one for `ident` (full hash once per process)."""
    path, proof_path = Path(path), Path(proof_path)
    try:
        if not path.exists() or not proof_path.exists():
            return False
        if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-journal')):
            return False
        stamp = (_signature(path), data._stamp(proof_path))
        with _mutex:
            if _verified.get(str(path)) == stamp:
                return True
        proof = json.loads(proof_path.read_text(encoding='utf-8'))
        if not isinstance(proof, dict) or proof.get('identity') != ident:
            return False
        if _file_sha256(path) != proof.get('sha256'):
            return False
        if (_signature(path), data._stamp(proof_path)) != stamp:
            return False
        with _mutex:
            _verified[str(path)] = stamp
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def totals(path):
    """The stored counts: {'all': n, '<money column>': observations where it is not zero}."""
    signature = _signature(path)
    with _mutex:
        known = _totals.get(str(path))
        if known and known[0] == signature:
            return dict(known[1])
    with closing(_readonly(path)) as db:
        value = json.loads(db.execute("SELECT value FROM metadata WHERE key='totals'").fetchone()[0])
    with _mutex:
        _totals[str(path)] = (signature, value)
    return dict(value)


def page(path, request, filters, where, params, column, all_rows, limit, offset):
    """(matching count, source IDs of the page in record order) for a covered selection.
    `where`/`params` are the records query's own row filter (with its basis condition); all_rows says
    that condition is absent (raw basis or include_excluded)."""
    refs = [item.get('field') for item in filters if isinstance(item, dict)]
    table = semantics.table('ordered', refs, request, original=True)
    with closing(_readonly(path)) as db:
        if not filters:
            count = totals(path)['all' if all_rows else column]
        else:
            count = db.execute('SELECT COUNT(*) FROM ' + table + ' WHERE ' + where, params).fetchone()[0]
        ids = [row[0] for row in db.execute('SELECT source_id FROM ' + table + ' WHERE ' + where +
                                            ' ORDER BY record_rank LIMIT ? OFFSET ?', list(params) + [limit, offset])]
    return count, ids


def _atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.' + uuid.uuid4().hex + '.pending')
    try:
        pending.write_bytes(payload)
        os.replace(pending, path)
    finally:
        if pending.exists():
            pending.unlink()


def build(index_path, ident, report=lambda processed, total, message: None, should_stop=None):
    """Build, prove and publish the sidecar for `ident` from the analytical index at `index_path`.
    report(processed, total, message) hears the progress (total None while it is not measurable).
    Returns the sidecar path. ValueError when the index changes or the copy cannot be proven."""
    index_path = Path(index_path)
    path, proof_path, _, _ = location(ident)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = tuple(ident['index'])
    if data._stamp(index_path) != stamp or not store.source_stable(index_path, stamp):
        raise ValueError('The contribution index changed before its records could be ordered. Retry.')
    started = time.perf_counter()
    pending = path.with_suffix('.' + uuid.uuid4().hex + '.pending')
    stop = should_stop or (lambda: False)
    try:
        index_sha = data._hash(index_path)
        with closing(sqlite3.connect(pending, uri=True)) as db:   # uri: ATTACH opens the index read-only
            db.execute('PRAGMA journal_mode=OFF')
            db.execute('PRAGMA synchronous=OFF')
            db.execute('ATTACH DATABASE ? AS original', (index_path.resolve().as_uri() + '?mode=ro',))
            declared = {row[1]: row[2] for row in db.execute('PRAGMA original.table_info(facts)')}
            missing = [name for name in COLUMNS if name not in declared]
            if missing:
                raise ValueError('The contribution index lacks record fields: ' + ', '.join(missing) + '.')
            # The declared types are the index's, so every comparison keeps the index's affinity.
            columns = ','.join('"' + name + '" ' + declared[name] for name in COLUMNS)
            names = ','.join('"' + name + '"' for name in COLUMNS)
            db.execute('CREATE TEMP TABLE staged(' + columns + ')')
            db.execute('CREATE TABLE ordered(record_rank INTEGER PRIMARY KEY,' + columns + ')')
            total = db.execute('SELECT COUNT(*) FROM original.facts').fetchone()[0]
            lowest, highest = db.execute('SELECT MIN(rowid),MAX(rowid) FROM original.facts').fetchone()
            copied = 0
            report(0, total, 'Copying the source records for inspection.')
            # Rowid ranges read the index front to back, and each batch is a measurable step.
            for low in range((lowest or 1) - 1, highest or 0, BATCH_ROWS):
                if stop():
                    raise ValueError('Record ordering stopped before it finished.')
                copied += db.execute('INSERT INTO temp.staged SELECT ' + names + ' FROM original.facts '
                                     'WHERE rowid>? AND rowid<=?', (low, low + BATCH_ROWS)).rowcount
                report(copied, total, 'Copying the source records for inspection.')
            if copied != total:
                raise ValueError('The contribution index changed while its records were copied. Retry.')
            report(0, None, 'Ordering the source records by date.')
            db.execute('INSERT INTO ordered(' + names + ') SELECT ' + names + ' FROM temp.staged '
                       'ORDER BY date DESC,source_id')
            db.execute('DROP TABLE temp.staged')
            if stop():
                raise ValueError('Record ordering stopped before it finished.')
            report(0, None, 'Verifying the ordered source records.')
            db.execute('CREATE UNIQUE INDEX ordered_source ON ordered(source_id)')
            for name in INDEXED:
                db.execute('CREATE INDEX ordered_' + name + ' ON ordered("' + name + '")')
            # Proof 1: identity. Every index observation has exactly one ordered row with the same values.
            checks = ['o.record_rank IS NULL']
            checks += ['o."' + name + '" IS NOT f."' + name + '"' for name in COLUMNS]
            checks += ['typeof(o."' + name + '") IS NOT typeof(f."' + name + '")' for name in COLUMNS]
            mismatch = db.execute('SELECT COUNT(*) FROM original.facts f LEFT JOIN ordered o '
                                  'ON o.source_id=f.source_id WHERE ' + ' OR '.join(checks)).fetchone()[0]
            count, first, final = db.execute('SELECT COUNT(*),MIN(record_rank),MAX(record_rank) FROM ordered').fetchone()
            absent = db.execute('SELECT COUNT(*) FROM ordered WHERE source_id IS NULL OR date IS NULL').fetchone()[0]
            if mismatch or count != total or absent or (total and (first, final) != (1, total)):
                raise ValueError('The records sidecar does not preserve every source observation.')
            # Proof 2: order. Consecutive ranks are strictly in (date DESC, source_id) order.
            disorder = db.execute('SELECT COUNT(*) FROM ordered a JOIN ordered b ON b.record_rank=a.record_rank+1 '
                                  'WHERE NOT (a.date>b.date OR (a.date=b.date AND a.source_id<b.source_id))').fetchone()[0]
            if disorder:
                raise ValueError('The records sidecar is not in record order.')
            counted = db.execute('SELECT COUNT(*),' + ','.join('COUNT(CASE WHEN ' + name + '!=0 THEN 1 END)' for name in MONEY[:-1]) +
                                 ' FROM ordered').fetchone()
            stored = {'all': counted[0], **dict(zip(MONEY[:-1], counted[1:]))}
            db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('INSERT INTO metadata VALUES (?,?)', ('totals', json.dumps(stored, sort_keys=True)))
            db.execute('INSERT INTO metadata VALUES (?,?)', ('identity', json.dumps(ident, sort_keys=True)))
            db.execute('ANALYZE main')   # the attached index is read-only
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('The records sidecar failed integrity verification.')
        if data._stamp(index_path) != stamp or not store.source_stable(index_path, stamp):
            raise ValueError('The contribution index changed while its records were ordered. Retry.')
        proof = {'identity': ident, 'sha256': _file_sha256(pending), 'source_index_sha256': index_sha,
                 'rows': total, 'totals': stored, 'seconds': round(time.perf_counter() - started, 3)}
        os.replace(pending, path)
        _atomic(proof_path, json.dumps(proof, sort_keys=True).encode('utf-8'))
        with _mutex:
            _verified[str(path)] = (_signature(path), data._stamp(proof_path))
        report(total, total, 'Source records ready.')
        return path
    finally:
        if pending.exists():
            pending.unlink()


def prune(keep=None, *, live=lambda lock: False, older_than=6 * 3600):
    """Remove sidecars whose analytical index is gone or has another stamp, and abandoned .pending files
    older than `older_than` seconds, skipping `keep` (a key) and any whose lock `live(lock)` says is held.
    Returns the names removed."""
    removed = []
    folder = root()
    if not folder.is_dir():
        return removed
    now = time.time()
    for proof_path in folder.glob('*.json'):
        name = proof_path.stem
        if name == keep:
            continue
        try:
            proof = json.loads(proof_path.read_text(encoding='utf-8'))
            index = proof['identity']['index']
            current = list(data._stamp(index[0])) if Path(index[0]).exists() else None
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            continue
        if current == index:
            continue   # its index is current (a console on other sidecar code may still read it)
        if live(proof_path.with_suffix('.lock')):
            continue
        for target in (proof_path.with_suffix('.sqlite'), proof_path):
            try:
                target.unlink(missing_ok=True)
                removed.append(target.name)
            except OSError:
                pass
    for pending in folder.glob('*.pending'):
        try:
            if now - pending.stat().st_mtime > older_than and not live(folder / (pending.name.split('.')[0] + '.lock')):
                pending.unlink()
                removed.append(pending.name)
        except OSError:
            pass
    return removed
