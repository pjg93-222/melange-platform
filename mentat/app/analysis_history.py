"""Local observation-vintage journal; event dates never become knowledge dates.

Stores hashes and timestamps, not contributor names or raw source copies. A first
inspection is a lower bound on this application's knowledge, not a filing date.
Transactions are committed only after a complete, stable analysis succeeds.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import queue
import sqlite3
import threading

import contribution_data as data

ROOT = data.PROJECT / 'state' / 'insights-history'
VERSION_FIELDS = ('source_id', 'company', 'committee_id', 'date', 'cycle',
                  'funding_source', 'contributor_key', 'recipient', 'party', 'industry',
                  'raw_cents', 'receipt_cents', 'original_cents',
                  'supported_cents', 'pac_cents')
NOTICE = ('First seen means first inspected by this local Insights journal. It is not '
          'the original publication date. Earlier uninspected evidence remains unknown.')
# Observed identities are merged into the journal in batches of this many rows.
BATCH_ROWS = 5000
# Batches waiting for the journal writer; a full queue makes the analysis wait for it.
QUEUED_BATCHES = 4
# One statement merges a batch exactly as per-row INSERT OR IGNORE followed by
# UPDATE last_seen=MAX(last_seen, stamp) did: an unseen identity is inserted with
# first_seen = last_seen = the inspection stamp, in arrival order (json_each yields the
# batch in order, ORDER BY key keeps it), so it takes the rowid a per-row insert would;
# a known identity keeps first_seen and takes MAX(last_seen, stamp).
_MERGE = ('INSERT INTO versions(source_id,version,first_seen,last_seen) '
          "SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]'),?,? FROM json_each(?) "
          'WHERE true ORDER BY key ON CONFLICT(source_id,version) '
          'DO UPDATE SET last_seen=MAX(versions.last_seen,excluded.last_seen)')
# UPSERT with a SELECT source, and json_each built in, need SQLite 3.38 or later.
_SET_MERGE = sqlite3.sqlite_version_info >= (3, 38, 0)
# SQLite gives a new row MAX(rowid)+1 until MAX(rowid) is 2**63-1, so the rows above an
# inspection's starting MAX(rowid) are exactly the versions it inserted. Below this
# starting point no inspection can come near that limit; above it (never reached in
# practice) the per-row statements count insertions instead.
_ROWID_HEADROOM = 2 ** 62

# json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) constructs
# this encoder on every call; encode() keeps no state between calls, so one shared
# instance produces the same text.
_ENCODER = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'))
_encode = _ENCODER.encode
_encode_string = json.encoder.encode_basestring  # what _ENCODER.encode does with a str
_sha256 = hashlib.sha256


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def _list_encoder():
    """The C encoder _ENCODER.encode builds for a list on every call, built once.

    markers=None drops only the circular-reference check. None when the C accelerator is
    missing or does not reproduce the pure-Python encoder on a probe of every value kind."""
    try:
        make = json.encoder.c_make_encoder
        encoder = make(None, _ENCODER.default, _encode_string, None, ':', ',', True, False, True)
        probe = ['a"\\\n\t\r\b\f\x00\x1f\x7f\u2028\u00e9\U0001f600', '', 0, -1, 2 ** 70, 1.5, -0.0,
                 1e300, float('nan'), float('inf'), -float('inf'), None, True, False,
                 {'b': 1, 'a': [None, 'x']}, [[]]]
        # iterencode() without _one_shot is the pure-Python encoder: an independent reference.
        if ''.join(encoder(probe, 0)) != ''.join(_ENCODER.iterencode(probe)):
            return None
        return encoder
    except Exception:  # noqa: BLE001 - no accelerator, another signature, another output
        return None


_LIST_ENCODER = _list_encoder()


def _values_text(values):
    """_ENCODER.encode(values). A value the shared C encoder cannot take (never a SQLite
    scalar) goes through encode() itself, which raises exactly what json.dumps raises."""
    if _LIST_ENCODER is not None:
        try:
            return ''.join(_LIST_ENCODER(values, 0))
        except Exception:  # noqa: BLE001 - the reference path decides
            pass
    return _encode(values)


def identity(row):
    """(digest(str(source_id)), digest(the row's VERSION_FIELDS values)), the same text
    hashed without rebuilding a JSON encoder for every digest."""
    return (_sha256(_encode_string(str(row['source_id'])).encode('utf-8')).hexdigest(),
            _sha256(_values_text(list(map(row.get, VERSION_FIELDS))).encode('utf-8')).hexdigest())


def _path(dataset):
    return ROOT / (digest(dataset) + '.sqlite')


class Inspection:
    """Records every observed row's (source, version) identity at this inspection's stamp.

    Identities are merged a batch at a time by one set-based statement. Once a first
    batch is full, a writer thread merges it while the analysis produces the next one.
    The connection is used by one thread at a time: the writer receives batches through
    a queue and is joined before the connection is used again (flush() and stop())."""

    def __init__(self, db, stamp):
        self.db, self.stamp, self.buffer = db, stamp, []
        self.observed = self.new_versions = 0
        self._top = None  # MAX(rowid) before the first set-based merge; None: none yet
        self._rows_only = not _SET_MERGE
        self._queue = self._writer = self._error = None

    def observe(self, rows):
        buffer, identify = self.buffer, identity
        for row in rows:
            buffer.append(identify(row))
            self.observed += 1
            if len(buffer) >= BATCH_ROWS:
                self._submit()
            yield row
        self.flush()

    def _merge(self, batch):
        """Merge one batch; runs on whichever single thread holds the connection."""
        if self._top is None and not self._rows_only:
            top = self.db.execute('SELECT COALESCE(MAX(rowid),0) FROM versions').fetchone()[0]
            if top >= _ROWID_HEADROOM:
                self._rows_only = True
            else:
                self._top = top
        if self._rows_only:
            self._merge_rows(batch)
        else:
            self.db.execute(_MERGE, (self.stamp, self.stamp, json.dumps(batch)))

    def _merge_rows(self, batch):
        """The per-row statements, for an SQLite without UPSERT-from-SELECT or json_each."""
        before = self.db.total_changes
        self.db.executemany('INSERT OR IGNORE INTO versions VALUES (?,?,?,?)',
                            [(sid, version, self.stamp, self.stamp) for sid, version in batch])
        self.new_versions += self.db.total_changes - before
        self.db.executemany('UPDATE versions SET last_seen=MAX(last_seen,?) WHERE source_id=? AND version=?',
                            [(self.stamp, sid, version) for sid, version in batch])

    def _write(self):
        while True:
            batch = self._queue.get()
            if batch is None:
                return
            # After a failed statement SQLite may have rolled the transaction back
            # itself, so nothing more is executed: a later statement would autocommit.
            if self._error is None:
                try:
                    self._merge(batch)
                except BaseException as error:  # noqa: BLE001 - raised to the consumer, which rolls back
                    self._error = error

    def _raise_writer_error(self):
        if self._error is not None:
            raise self._error

    def _submit(self):
        """Hand the buffered identities to the writer thread, starting it once."""
        self._raise_writer_error()
        batch = self.buffer[:]
        self.buffer.clear()
        if self._writer is None:
            self._queue = queue.Queue(maxsize=QUEUED_BATCHES)
            self._writer = threading.Thread(target=self._write, name='mentat-insights-journal', daemon=True)
            self._writer.start()
        self._queue.put(batch)

    def stop(self):
        """Wait until the writer has merged what it was given and exited."""
        if self._writer is not None:
            self._queue.put(None)
            self._writer.join()
            self._writer = self._queue = None

    def flush(self):
        """Merge every identity observed so far, then count the versions inserted."""
        if self._writer is not None:
            if self.buffer:
                self._submit()
            self.stop()
        self._raise_writer_error()
        if self.buffer:
            batch = self.buffer[:]
            self.buffer.clear()
            self._merge(batch)
        if self._top is not None:
            self.new_versions = self.db.execute('SELECT COUNT(*) FROM versions WHERE rowid>?',
                                                (self._top,)).fetchone()[0]

    def summary(self):
        return {'inspected_at': self.stamp, 'observations_inspected': self.observed,
                'newly_inspected_versions': self.new_versions, 'notice': NOTICE}


@contextmanager
def inspection(dataset, token, provenance, now=None):
    ROOT.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the inspection's writer thread merges on this connection
    # while the analysis runs; Inspection never lets two threads use it at once.
    db = sqlite3.connect(_path(dataset), timeout=120, check_same_thread=False)
    journal = None
    try:
        # Hash keys distribute writes throughout the B-tree. Keep a bounded
        # working set instead of repeatedly paging the default 2 MiB cache.
        # A complete industry journal is ~220 MiB. Below that working set,
        # random version-hash updates repeatedly evict dirty pages and turn
        # one inspection into minutes of disk I/O. One Insights worker owns
        # this bounded cache; SQLite allocates only the pages it needs.
        db.execute('PRAGMA cache_size=-262144')
        db.executescript('''CREATE TABLE IF NOT EXISTS versions (
          source_id TEXT, version TEXT, first_seen TEXT, last_seen TEXT,
          PRIMARY KEY(source_id,version));
          CREATE TABLE IF NOT EXISTS snapshots (
          token TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, provenance TEXT);''')
        db.execute('BEGIN IMMEDIATE')
        # The inspection clock starts after waiting for another writer. Clock
        # corrections also must not move a recorded last inspection backwards.
        stamp = now or datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO snapshots VALUES (?,?,?,?) ON CONFLICT(token) DO UPDATE SET last_seen=MAX(snapshots.last_seen,excluded.last_seen)',
                   (token, stamp, stamp, json.dumps(provenance, sort_keys=True)))
        journal = Inspection(db, stamp)
        yield journal
        journal.flush()
        db.commit()
    except BaseException:
        if journal is not None:
            journal.stop()  # the writer is idle before the rollback uses the connection
        db.rollback()
        raise
    finally:
        if journal is not None:
            journal.stop()
        db.close()


def annotate(dataset, rows):
    path = _path(dataset)
    if not path.is_file():
        return rows
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        for row in rows:
            found = db.execute('SELECT first_seen,last_seen FROM versions WHERE source_id=? AND version=?', identity(row)).fetchone()
            row['first_seen_by_insights'] = found[0] if found else None
            row['last_seen_by_insights'] = found[1] if found else None
        return rows
    finally:
        db.close()


def snapshots(dataset, limit=30):
    path = _path(dataset)
    if not path.is_file():
        return []
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return [{'index_token': token, 'first_seen': first, 'last_seen': last,
                 'provenance': json.loads(provenance)} for token, first, last, provenance in
                db.execute('SELECT * FROM snapshots ORDER BY first_seen DESC LIMIT ?', (limit,))]
    finally:
        db.close()
