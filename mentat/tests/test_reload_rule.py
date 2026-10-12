"""F11 (Paul 2026-09-25): an FEC reload counts once; nothing that merely looks like one does.

A reload is one record the FEC loaded twice under two SUB_IDs: same committee, report type and
transaction id, same file number, same identity (counterparty id and name, amount, date, type) and
memo code; a row without a transaction id or a file number is never one.
tools/fec_transfer_graph.py reload_key() is the key for OTH transfers; R/fec_beneficiary.R's
FEC_PAS2_RELOAD_KEY is the same key for PAS2 (tests/test_pas2_reload_rule.R). Kept counted:
  * genuine repeats, the same identity under DISTINCT transaction ids (DAJ 2026: 16 groups);
  * cross-file observations, the same identity in two filings (CLF 2022 Barrett, H2MI07123:
    two $50,000 items in files 1592379 and 1598276);
  * a same-file pair whose memo codes differ (a memo line is not a second copy of a real one).
Every case has a negative control: the rule weakened the way a regression would weaken it, and
the same assertion failing on the result.
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from unittest import mock

MENTAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MENTAT / 'tools'))
import fec_transfer_graph  # noqa: E402

RSCRIPT = Path('C:/Program Files/R/R-4.6.0/bin/Rscript.exe')


def oth(sub, file='1592379', transaction='SE.5', amount='50000', date='06152022', dest='C00000002', memo='',
        name='PAC TWO'):
    fields = [''] * 21
    for index, value in {0: 'C00000001', 1: 'N', 2: 'Q3', 5: '24K', 7: name, 13: date, 14: amount, 15: dest,
                         16: transaction, 17: file, 18: memo, 20: sub}.items():
        fields[index] = value
    return '|'.join(fields)


class OthReloads(unittest.TestCase):
    def build(self, *lines):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'itoth.txt'
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        index = fec_transfer_graph.transfer_index(path, 2022)
        with closing(sqlite3.connect(index['ledger_path'])) as connection:
            statuses = {row[0]: (row[1], row[2]) for row in
                        connection.execute('SELECT sub_id, status, version_basis FROM records')}
        return index, statuses

    def counted(self, index):
        return sum(edge['amount'] for edge in index['edges']), sum(edge['record_count'] for edge in index['edges'])

    # --- the assertions each case makes; each negative control re-runs one on weakened code
    def assert_reload_counted_once(self, index, statuses):
        self.assertEqual(self.counted(index), (50000, 1))
        self.assertEqual(statuses, {'1': ('duplicate', 'repeated_itemization'), '2': ('current', 'distinct_observation')})

    def assert_both_counted(self, index, statuses, amount=100000):
        self.assertEqual(self.counted(index), (amount, 2))
        self.assertTrue(all(status == 'current' for status, _ in statuses.values()), statuses)

    def test_a_reload_counts_once_and_keeps_the_highest_sub_id(self):
        self.assert_reload_counted_once(*self.build(oth('1'), oth('2')))
        self.assertEqual(fec_transfer_graph.VERSION, 6)

    def test_negative_control_a_key_that_includes_the_sub_id_counts_the_reload_twice(self):
        original = fec_transfer_graph.reload_key
        with mock.patch.object(fec_transfer_graph, 'reload_key', lambda row: (*original(row), row['sub_id'])):
            result = self.build(oth('1'), oth('2'))
        with self.assertRaises(AssertionError):
            self.assert_reload_counted_once(*result)

    def test_genuine_repeats_under_distinct_transaction_ids_are_both_counted(self):
        self.assert_both_counted(*self.build(oth('1', transaction='SE.4201'), oth('2', transaction='SE.4202')))

    def test_negative_control_repeats_whose_transaction_ids_are_ignored_collapse(self):
        # Group without the transaction id: the two genuine repeats then share every part of the
        # reload key, and the rule would count them once. The assertion above must catch that.
        grouping = lambda fields, number: '|'.join((fields[0], fields[2]))
        with mock.patch.object(fec_transfer_graph, 'transfer_key', grouping):
            result = self.build(oth('1', transaction='SE.4201'), oth('2', transaction='SE.4202'))
        with self.assertRaises(AssertionError):
            self.assert_both_counted(*result)

    def test_the_barrett_shape_same_identity_in_two_filings_is_both_counted_and_flagged(self):
        index, statuses = self.build(oth('1', file='1592379'), oth('2', file='1598276'))
        self.assert_both_counted(index, statuses)
        self.assertEqual(set(statuses.values()), {('current', 'filing_relationship_unverified')})

    def test_negative_control_a_key_without_the_file_number_collapses_the_barrett_pair(self):
        original = fec_transfer_graph.reload_key
        with mock.patch.object(fec_transfer_graph, 'reload_key',
                               lambda row: tuple(v for i, v in enumerate(original(row)) if i != 1)):
            result = self.build(oth('1', file='1592379'), oth('2', file='1598276'))
        with self.assertRaises(AssertionError):
            self.assert_both_counted(*result)

    def test_a_memo_line_is_not_a_reload_of_the_real_line(self):
        index, statuses = self.build(oth('1'), oth('2', memo='X'))
        self.assertEqual(self.counted(index), (50000, 1))  # the memo line never counts; the real one must
        self.assertEqual(statuses['1'][0], 'current')

    def test_a_different_payee_name_or_a_missing_file_number_is_not_a_reload(self):
        self.assert_both_counted(*self.build(oth('1', name='PAC TWO'), oth('2', name='PAC TWO VICTORY FUND')))
        self.assert_both_counted(*self.build(oth('1', file=''), oth('2', file='')))

    def test_negative_control_a_key_without_the_name_merges_two_payees(self):
        original = fec_transfer_graph.reload_key
        with mock.patch.object(fec_transfer_graph, 'reload_key', lambda row: original(row)[:-2] + original(row)[-1:]):
            result = self.build(oth('1', name='PAC TWO'), oth('2', name='PAC TWO VICTORY FUND'))
        with self.assertRaises(AssertionError):
            self.assert_both_counted(*result)

    def test_negative_control_a_key_without_the_memo_code_drops_the_real_line(self):
        original = fec_transfer_graph.reload_key
        with mock.patch.object(fec_transfer_graph, 'reload_key', lambda row: original(row)[:-1]):
            index, statuses = self.build(oth('1'), oth('2', memo='X'))
        self.assertEqual(self.counted(index), (0, 0))
        self.assertEqual(statuses['1'][0], 'duplicate')


@unittest.skipUnless(RSCRIPT.is_file(), 'The PAS2 half needs R 4.6 at C:/Program Files/R/R-4.6.0')
class Pas2Reloads(unittest.TestCase):
    def test_the_pas2_suite_passes_including_its_negative_controls(self):
        result = subprocess.run([str(RSCRIPT), str(MENTAT / 'tests' / 'test_pas2_reload_rule.R')],
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PAS2 reload rule: PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
