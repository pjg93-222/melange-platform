"""First inspection is distinct from event/publication time; failures roll back."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import analysis_history as h


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        p = patch.object(h, 'ROOT', Path(self.tmp.name)); p.start(); self.addCleanup(p.stop)
        self.row = {'source_id': 'receipt-1', 'date': '2024-01-01', 'receipt_cents': 1000,
                    'company': 'A', 'contributor': 'Private source label'}

    def inspect(self, token, row, now):
        with h.inspection('Example', token, {'workbook_sha256': token}, now=now) as journal:
            list(journal.observe([row]))
            return journal.summary()

    def test_versions_keep_first_inspection_and_separate_amendments(self):
        first = self.inspect('v1', self.row, '2026-09-20T10:00:00+00:00')
        again = self.inspect('v2', self.row, '2026-09-21T10:00:00+00:00')
        changed = {**self.row, 'receipt_cents': 2000}
        amended = self.inspect('v3', changed, '2026-09-22T10:00:00+00:00')
        self.assertEqual([first['newly_inspected_versions'], again['newly_inspected_versions'], amended['newly_inspected_versions']], [1, 0, 1])
        original = h.annotate('Example', [dict(self.row)])[0]
        self.assertEqual(original['first_seen_by_insights'], '2026-09-20T10:00:00+00:00')
        self.assertEqual(original['last_seen_by_insights'], '2026-09-21T10:00:00+00:00')
        self.assertEqual(len(h.snapshots('Example')), 3)
        self.assertNotIn(b'Private source label', h._path('Example').read_bytes())

    def test_failed_analysis_publishes_neither_snapshot_nor_first_seen(self):
        with self.assertRaises(RuntimeError):
            with h.inspection('Example', 'failed', {}) as journal:
                list(journal.observe([self.row]))
                raise RuntimeError('source changed')
        self.assertEqual(h.snapshots('Example'), [])
        self.assertIsNone(h.annotate('Example', [dict(self.row)])[0]['first_seen_by_insights'])

    def test_identity_correction_changes_version_without_storing_identity(self):
        one = {**self.row, 'contributor_key': 'private donor identity one'}
        two = {**self.row, 'contributor_key': 'private donor identity two'}
        self.inspect('v1', one, '2026-09-20T10:00:00+00:00')
        corrected = self.inspect('v2', two, '2026-09-21T10:00:00+00:00')
        self.assertEqual(corrected['newly_inspected_versions'], 1)
        self.assertEqual(h.annotate('Example', [two])[0]['first_seen_by_insights'], '2026-09-21T10:00:00+00:00')
        self.assertNotIn(b'private donor identity', h._path('Example').read_bytes())

    def test_backwards_clock_cannot_move_last_inspection_before_first(self):
        self.inspect('v1', self.row, '2026-09-21T10:00:00+00:00')
        self.inspect('v1', self.row, '2026-09-20T10:00:00+00:00')
        row = h.annotate('Example', [dict(self.row)])[0]
        self.assertEqual(row['last_seen_by_insights'], '2026-09-21T10:00:00+00:00')
        self.assertEqual(h.snapshots('Example')[0]['last_seen'], '2026-09-21T10:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
