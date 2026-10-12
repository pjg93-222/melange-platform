"""access_stats (the RACF value ledger) and the value-ordered eviction of contribution_query_store._prune
and response_cache's disk tier. MENTAT_VALUE_TEST_OVERLAY names a folder whose modules replace the app's
(a staged or deliberately broken variant)."""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
if os.environ.get('MENTAT_VALUE_TEST_OVERLAY'):
    sys.path.insert(0, os.environ['MENTAT_VALUE_TEST_OVERLAY'])
import access_stats  # noqa: E402
import contribution_query_store as store  # noqa: E402
import response_cache as rc  # noqa: E402

DAY = 24 * 3600
WARM = 946684800 * 10 ** 9 + 5 * 10 ** 12   # inside prewarm's demoted range


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(access_stats.configure, None)
        self.db = Path(self.tmp.name) / 'access.sqlite'
        access_stats.configure(self.db)
        self.enterContext(patch.dict(os.environ, {'MENTAT_ACCESS_STATS': '1'}))

    def row(self, kind, key):
        with closing(sqlite3.connect(self.db)) as db, db:
            return db.execute('SELECT freq, last_ts, count, cost_s, size, body FROM access WHERE kind=? AND key=?',
                              (kind, key)).fetchone()


class AccessStatsTest(Ledger):
    def test_decay_halves_a_weeks_old_access(self):
        access_stats.record('k', 'now')
        self.assertTrue(access_stats.flush())
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('INSERT INTO access VALUES (?,?,?,?,?,?,?,?)', ('k', 'old', 1.0, time.time() - 7 * DAY, 1,
                                                                        None, None, None))
        now, old = access_stats.score('k', 'now'), access_stats.score('k', 'old')
        self.assertAlmostEqual(now, 1.0 * 0.05 / 0.01, places=3)
        self.assertAlmostEqual(old / now, 0.5, places=3)
        for _ in range(2):
            access_stats.record('k', 'now')
        access_stats.flush()
        freq, _, count, *_ = self.row('k', 'now')
        self.assertEqual(count, 3)
        self.assertAlmostEqual(freq, 3.0, places=3)

    def test_top_orders_by_frequency_cost_and_size(self):
        access_stats.record('q', 'cheap-big', body={'a': 1}, cost_s=2.0, size=1024 * 1024)       # 1 x 2 / 1
        access_stats.record('q', 'dear-small', body={'b': 2}, cost_s=2.0, size=10 * 1024)      # 1 x 2 / .01
        for _ in range(3):
            access_stats.record('q', 'frequent')                                                # 3 x .05 / .01
        access_stats.record('other', 'x', cost_s=100.0, size=1)
        ranked = access_stats.top(['q'])
        self.assertEqual([row['key'] for row in ranked], ['dear-small', 'frequent', 'cheap-big'])
        self.assertEqual(ranked[0]['body'], {'b': 2})
        self.assertEqual(ranked[1]['count'], 3)
        self.assertEqual(set(ranked[0]), {'kind', 'key', 'body', 'score', 'count', 'last_utc', 'cost_s', 'size'})
        self.assertEqual(access_stats.top(None, limit=1)[0]['key'], 'x')
        self.assertEqual(access_stats.score('q', 'unknown'), 0.0)
        access_stats.record('q', 'cheap-big')            # a later access without measurements keeps them
        access_stats.flush()
        self.assertEqual(self.row('q', 'cheap-big')[3:5], (2.0, 1024 * 1024))

    def test_two_processes_lose_no_access(self):
        script = ('import sys\nsys.path[:0]=[sys.argv[4], sys.argv[3]] if sys.argv[4] else [sys.argv[3]]\n'
                  'import access_stats as a\na.configure(sys.argv[1])\n'
                  'for i in range(300):\n    a.record("p", "shared")\n    a.record("p", "own"+sys.argv[2])\n'
                  '    a.flush() if i % 7 == 0 else None\n')
        overlay = os.environ.get('MENTAT_VALUE_TEST_OVERLAY', '')
        children = [subprocess.Popen([sys.executable, '-c', script, str(self.db), str(n), str(APP), overlay])
                    for n in range(2)]
        self.assertEqual([child.wait(timeout=120) for child in children], [0, 0])
        freq, _, count, *_ = self.row('p', 'shared')
        self.assertEqual(count, 600)
        self.assertAlmostEqual(freq, 600.0, delta=0.01)
        self.assertEqual([self.row('p', 'own0')[2], self.row('p', 'own1')[2]], [300, 300])

    def test_kill_switch(self):
        with patch.dict(os.environ, {'MENTAT_ACCESS_STATS': '0'}):
            access_stats.record('k', 'off', cost_s=1.0)
            access_stats.flush()
        self.assertEqual(access_stats.score('k', 'off'), 0.0)
        self.assertEqual(access_stats.top(), [])

    def test_never_raises(self):
        access_stats.record('k', 'odd', body={'x': object()}, cost_s=float('nan'), size=-3)
        access_stats.record(None, object())
        self.assertTrue(access_stats.flush())
        self.assertIsNone(self.row('k', 'odd')[5])
        self.assertEqual(self.row('k', 'odd')[3:5], (None, None))
        access_stats.configure(Path(self.tmp.name))            # a folder: every flush fails
        access_stats.record('k', 'lost')
        self.assertFalse(access_stats.flush())
        broken = Path(self.tmp.name) / 'broken.sqlite'
        broken.write_bytes(b'not a database' * 100)
        access_stats.configure(broken)
        self.assertEqual(access_stats.score('k', 'x'), 0.0)
        self.assertEqual(access_stats.top(), [])
        access_stats.record('k', 'y')
        self.assertFalse(access_stats.flush())


def _name(i):
    return hashlib.sha256(str(i).encode()).hexdigest() + '.json'


class StorePruneTest(Ledger):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(store, 'ROOT', Path(self.tmp.name) / 'query-cache'))
        store._usage.clear()
        self.addCleanup(store._usage.clear)

    def folder(self, namespace, mtimes, size=100):
        folder = store.ROOT / 'responses' / namespace
        folder.mkdir(parents=True)
        for i, mtime in enumerate(mtimes):
            path = folder / _name(i)
            path.write_bytes(b'x' * size)
            os.utime(path, ns=(mtime, mtime))
        return folder

    def test_value_order(self):
        now = time.time_ns()
        hour = 3600 * 10 ** 9
        # 0,1 warm (0 older); 2 high score but the oldest click; 3 medium; 4,5 unscored (5 older); 6 fresh
        folder = self.folder('val', [WARM, WARM + 10 ** 9, now - 10 * hour, now - 4 * hour, now - 3 * hour,
                                     now - 6 * hour, now])
        for key, hits in ((_name(2), 20), (_name(3), 5)):
            for _ in range(hits):
                access_stats.record('store/val', key[:-5])
        with patch.object(store, 'RESPONSE_KEEP_FILES', 4):
            removed = store._prune(folder)
        self.assertEqual(removed, [_name(0), _name(1), _name(5)])
        self.assertEqual({p.name for p in folder.glob('*.json')}, {_name(2), _name(3), _name(4), _name(6)})

    def test_caps(self):
        now = time.time_ns()
        folder = self.folder('cap', [now - (2000 - i) * 10 ** 9 for i in range(1030)], size=10)
        self.assertEqual((store.RESPONSE_KEEP_FILES, store.RESPONSE_KEEP_BYTES), (1024, 512 * 1024 * 1024))
        self.assertEqual(sorted(store._prune(folder)), sorted(_name(i) for i in range(6)))
        self.assertEqual(len(list(folder.glob('*.json'))), 1024)
        with patch.object(store, 'RESPONSE_KEEP_BYTES', 100):
            store._prune(folder, keep=_name(10))
        self.assertEqual({p.name for p in folder.glob('*.json')}, {_name(10)} | {_name(i) for i in range(1021, 1030)})

    def test_writes_are_amortized(self):
        scans = []
        real = store._responses
        with patch.object(store, '_responses', side_effect=lambda d: scans.append(d) or real(d)):
            for i in range(40):
                self.assertEqual(store.cached('amortized', {'i': i}, lambda i=i: {'value': i}), {'value': i})
        self.assertEqual(len(scans), 1)            # the first write counts the folder; the rest are O(1)
        self.assertEqual(store._usage[str(store.ROOT / 'responses' / 'amortized')][:3], [40, sum(
            p.stat().st_size for p in (store.ROOT / 'responses' / 'amortized').glob('*.json')), 39])
        ranked = access_stats.scores('store/amortized')
        self.assertEqual(len(ranked), 40)          # every build recorded its cost and size


class ResponseCacheEvictionTest(Ledger):
    def setUp(self):
        super().setUp()
        saved = (rc.ROOT, dict(rc.DISK_LIMITS))
        self.addCleanup(lambda: (setattr(rc, 'ROOT', saved[0]), rc.DISK_LIMITS.clear(),
                                 rc.DISK_LIMITS.update(saved[1]), rc.clear_memory()))
        rc.configure(Path(self.tmp.name) / 'rc')
        rc.clear_memory()

    def test_warm_then_lowest_score_then_lru(self):
        body = lambda i: {'i': i, 'pad': hashlib.sha256(b'same').hexdigest() * 40}   # noqa: E731
        keys = [rc.entry_key('val', {'i': i}, {}) for i in range(4)]
        for i in range(3):
            rc.get_or_compute('val', {'i': i}, {}, lambda i=i: body(i), memory=False)
        folder = Path(rc.ROOT) / 'val'
        size = rc.disk_usage('val')['bytes'] / 3
        base = time.time() - 5 * 3600
        for i in range(3):
            os.utime(folder / (keys[i] + '.json'), (base + i * 60, base + i * 60))
        os.utime(folder / (keys[1] + '.json'), ns=(WARM, WARM))                     # warm, never used
        for _ in range(50):
            access_stats.record('cache/val', keys[0])                              # the least recently used
        rc.DISK_LIMITS['val'] = int(size * 2.5)
        rc.get_or_compute('val', {'i': 3}, {}, lambda: body(3), memory=False)
        present = {i for i in range(4) if (folder / (keys[i] + '.json')).exists()}
        self.assertEqual(present, {0, 3})
        self.assertGreater(access_stats.score('cache/val', keys[3]), 0.0)          # its build was recorded


if __name__ == '__main__':
    unittest.main()
