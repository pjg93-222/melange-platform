"""The observation journal's set-based, pipelined merge leaves exactly the journal the per-row
statements left: the same rows, rowids, first_seen and last_seen, the same snapshots, the same
summary; identities hash the same text; a failure anywhere rolls everything back."""
import hashlib
import json
from pathlib import Path
import random
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import analysis_history as h  # noqa: E402

STAMPS = ['2026-09-20T10:00:00+00:00', '2026-09-21T10:00:00+00:00', '2026-09-19T10:00:00+00:00']


def reference_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def reference_identity(row):
    return reference_digest(str(row['source_id'])), reference_digest([row.get(key) for key in h.VERSION_FIELDS])


def reference_inspect(path, token, provenance, stamp, rows):
    """The journal code as it stood before this change (analysis_history.py at 562195810):
    INSERT OR IGNORE then UPDATE last_seen=MAX(last_seen, ?) for every 2000 rows."""
    db = sqlite3.connect(path, timeout=120)
    try:
        db.execute('PRAGMA cache_size=-262144')
        db.executescript('''CREATE TABLE IF NOT EXISTS versions (
          source_id TEXT, version TEXT, first_seen TEXT, last_seen TEXT,
          PRIMARY KEY(source_id,version));
          CREATE TABLE IF NOT EXISTS snapshots (
          token TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, provenance TEXT);''')
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO snapshots VALUES (?,?,?,?) ON CONFLICT(token) DO UPDATE SET last_seen=MAX(snapshots.last_seen,excluded.last_seen)',
                   (token, stamp, stamp, json.dumps(provenance, sort_keys=True)))
        buffer, observed, new = [], 0, 0

        def flush():
            nonlocal new
            if not buffer:
                return
            before = db.total_changes
            db.executemany('INSERT OR IGNORE INTO versions VALUES (?,?,?,?)', buffer)
            new += db.total_changes - before
            db.executemany('UPDATE versions SET last_seen=MAX(last_seen,?) WHERE source_id=? AND version=?',
                           ((last, sid, ver) for sid, ver, first, last in buffer))
            buffer.clear()
        for row in rows:
            sid, version = reference_identity(row)
            buffer.append((sid, version, stamp, stamp))
            observed += 1
            if len(buffer) >= 2000:
                flush()
        flush()
        db.commit()
        return {'inspected_at': stamp, 'observations_inspected': observed, 'newly_inspected_versions': new,
                'notice': h.NOTICE}
    finally:
        db.close()


def dump(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        lines = [json.dumps(['schema', name, sql]) for name, sql in db.execute('SELECT name, sql FROM sqlite_master ORDER BY type, name')]
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
            lines += [json.dumps([table, *row]) for row in db.execute(f'SELECT rowid,* FROM "{table}" ORDER BY rowid')]
        return lines
    finally:
        db.close()


def make_rows(seed, count, companies=('A', 'B', 'C')):
    rng = random.Random(seed)
    rows = []
    for index in range(count):
        rows.append({'source_id': f'receipt-{rng.randrange(count * 2)}', 'company': rng.choice(companies),
                     'committee_id': f'C{rng.randrange(10**8):08d}', 'date': f'2024-0{rng.randrange(1, 10)}-1{rng.randrange(10)}',
                     'cycle': '2023-2024', 'funding_source': rng.choice(['employee_original', 'corporate_original']),
                     'contributor_key': f'reported:NAME {rng.randrange(50)}|MN|55401', 'recipient': 'Committee "One"\\ \u00e9',
                     'party': rng.choice(['DEM', 'REP', '']), 'industry': 'Banking', 'raw_cents': rng.randrange(-5000, 90000),
                     'receipt_cents': rng.randrange(0, 90000), 'original_cents': 0, 'supported_cents': 0, 'pac_cents': 0,
                     'amount_cents': 1, 'extra_field': index})
    return rows


class IdentityTests(unittest.TestCase):
    def test_identity_hashes_exactly_what_json_dumps_hashed(self):
        values = ['', 'plain', 'quote " and backslash \\', 'controls \x00\x01\x1f\x7f \n\r\t\b\f', '\u2028\u2029',
                  'accents \u00e9\u00fc and emoji \U0001f600', 0, -1, 7, 2 ** 70, -(2 ** 64), 1.5, -0.0, 1e300, 1e-300,
                  float('nan'), float('inf'), -float('inf'), None, True, False, 12345678901234567890]
        rng = random.Random(7)
        rows = []
        for index in range(600):
            row = {key: rng.choice(values) for key in h.VERSION_FIELDS if rng.random() > 0.1}
            row['source_id'] = rng.choice([index, f's{index}', 'x"y', 3.5, True, None, '\u00e9'])
            rows.append(row)
        rows.append({'source_id': 1, 'company': ['nested', {'b': 1, 'a': 2}]})
        for row in rows:
            self.assertEqual(h.identity(row), reference_identity(row), row)
        self.assertEqual(h.digest({'b': [1, 'x'], 'a': None}), reference_digest({'b': [1, 'x'], 'a': None}))

    def test_an_unencodable_value_fails_as_json_dumps_does(self):
        row = {'source_id': 's', 'company': b'bytes'}
        with self.assertRaises(TypeError) as expected:
            reference_identity(row)
        with self.assertRaises(TypeError) as actual:
            h.identity(row)
        self.assertEqual(str(actual.exception), str(expected.exception))
        loop = []
        loop.append(loop)
        with self.assertRaises(ValueError):
            h.identity({'source_id': 's', 'company': loop})

    def test_the_shared_c_encoder_is_in_use_and_checked(self):
        self.assertIsNotNone(h._LIST_ENCODER, 'the C list encoder should reproduce json.dumps on this build')
        with patch.object(h.json.encoder, 'c_make_encoder', lambda *args: (lambda value, level: ('WRONG',))):
            self.assertIsNone(h._list_encoder())


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.new_root = self.tmp / 'new'
        self.old_root = self.tmp / 'old'
        self.new_root.mkdir()
        self.old_root.mkdir()
        patcher = patch.object(h, 'ROOT', self.new_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def new_inspect(self, token, stamp, rows, consume=list):
        with h.inspection('Example', token, {'token': token}, now=stamp) as journal:
            consume(journal.observe(rows))
            return journal.summary()

    def old_inspect(self, token, stamp, rows):
        return reference_inspect(self.old_root / h._path('Example').name, token, {'token': token}, stamp, rows)

    def assert_same_journal(self):
        self.assertEqual(dump(h._path('Example')), dump(self.old_root / h._path('Example').name))

    def seed_both(self, rows, stamp, token='seed'):
        self.assertEqual(self.new_inspect(token, stamp, rows), self.old_inspect(token, stamp, rows))

    def test_one_inspection_matches_the_per_row_statements(self):
        rows = make_rows(1, 900)
        self.assertEqual(self.new_inspect('v1', STAMPS[0], rows), self.old_inspect('v1', STAMPS[0], rows))
        self.assert_same_journal()

    def test_threaded_batches_known_rows_new_rows_duplicates_and_a_backwards_clock(self):
        with patch.object(h, 'BATCH_ROWS', 7), patch.object(h, 'QUEUED_BATCHES', 2):
            first = make_rows(2, 300)
            self.seed_both(first, STAMPS[0])
            second = make_rows(3, 450) + first[:120] + first[:40]          # new, known and repeated identities
            self.assertEqual(self.new_inspect('v2', STAMPS[1], second), self.old_inspect('v2', STAMPS[1], second))
            third = first[100:260] + make_rows(4, 90)                      # an earlier clock than the last inspection
            self.assertEqual(self.new_inspect('v1', STAMPS[2], third), self.old_inspect('v1', STAMPS[2], third))
        self.assert_same_journal()

    def test_a_null_last_seen_and_an_empty_scope_are_kept_as_they_were(self):
        rows = make_rows(5, 60)
        self.seed_both(rows, STAMPS[0])
        for path in (h._path('Example'), self.old_root / h._path('Example').name):
            with sqlite3.connect(path) as db:
                db.execute('UPDATE versions SET last_seen=NULL WHERE rowid % 7 = 0')
        with patch.object(h, 'BATCH_ROWS', 5):
            self.assertEqual(self.new_inspect('v2', STAMPS[1], rows), self.old_inspect('v2', STAMPS[1], rows))
            self.assertEqual(self.new_inspect('v3', STAMPS[1], []), self.old_inspect('v3', STAMPS[1], []))
        self.assert_same_journal()

    def test_the_per_row_fallback_and_the_rowid_headroom_path_match_too(self):
        rows = make_rows(6, 200)
        with patch.object(h, '_SET_MERGE', False), patch.object(h, 'BATCH_ROWS', 11):
            self.assertEqual(self.new_inspect('v1', STAMPS[0], rows), self.old_inspect('v1', STAMPS[0], rows))
        more = make_rows(7, 150) + rows[:30]
        with patch.object(h, '_ROWID_HEADROOM', 1), patch.object(h, 'BATCH_ROWS', 11):
            self.assertEqual(self.new_inspect('v2', STAMPS[1], more), self.old_inspect('v2', STAMPS[1], more))
        self.assert_same_journal()

    def test_a_writer_failure_rolls_everything_back_and_nothing_runs_after_it(self):
        rows = make_rows(8, 100)
        self.seed_both(rows[:10], STAMPS[0])
        before = dump(h._path('Example'))
        merges = []
        real = h.Inspection._merge

        def failing(journal, batch):
            merges.append(threading.current_thread().name)
            if len(merges) == 2:
                raise sqlite3.OperationalError('disk I/O error (test)')
            return real(journal, batch)
        with patch.object(h, 'BATCH_ROWS', 10), patch.object(h.Inspection, '_merge', failing):
            with self.assertRaises(sqlite3.OperationalError):
                self.new_inspect('v2', STAMPS[1], rows)
        self.assertEqual(len(merges), 2, 'no batch may be merged after a failed one')
        self.assertEqual(dump(h._path('Example')), before)
        self.assertFalse([t for t in threading.enumerate() if t.name == 'mentat-insights-journal' and t.is_alive()])

    def test_an_analysis_that_stops_early_rolls_back_with_the_writer_stopped_first(self):
        rows = make_rows(9, 400)
        self.seed_both(rows[:50], STAMPS[0])
        before = dump(h._path('Example'))

        def abandon(iterator):
            for index, _ in enumerate(iterator):
                if index == 333:
                    raise RuntimeError('capacity reached (test)')
        with patch.object(h, 'BATCH_ROWS', 13):
            with self.assertRaises(RuntimeError):
                self.new_inspect('v2', STAMPS[1], rows, consume=abandon)
        self.assertEqual(dump(h._path('Example')), before)
        self.assertEqual(len(h.snapshots('Example')), 1)
        self.assertFalse([t for t in threading.enumerate() if t.name == 'mentat-insights-journal' and t.is_alive()])

    def test_annotate_reads_what_the_merge_wrote(self):
        rows = make_rows(10, 30)
        with patch.object(h, 'BATCH_ROWS', 4):
            self.new_inspect('v1', STAMPS[0], rows)
            self.new_inspect('v2', STAMPS[1], rows[:5])
        annotated = h.annotate('Example', [dict(row) for row in rows[:8]])
        self.assertEqual([row['last_seen_by_insights'] for row in annotated[:5]], [STAMPS[1]] * 5)
        self.assertTrue(all(row['first_seen_by_insights'] == STAMPS[0] for row in annotated))


if __name__ == '__main__':
    unittest.main()
