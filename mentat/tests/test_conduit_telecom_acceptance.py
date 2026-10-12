"""Acceptance: on the real Telecom data, ActBlue is a Conduit and the audited Super PAC figure holds.

Drives the console's own query code in-process and read-only: the source is resolved the way the console
resolves it (contribution_data._sources), the existing analytical index and query view are used as they are
(this test never builds one and never starts a build thread), and the response cache is bypassed so
nothing is written under state\\.

Telecom 2025-2026, Published included receipts ('receipt'), grouped by recipient_category. The audited
workbook (reports\\) says Super PAC $50,202.51; the facts store built from the stale published payload
files ActBlue ($1,594,803.86) and WinRed ($244,292.23) under Super PAC as well.
"""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'app'))
import contribution_data as data
import contribution_query as query
import contribution_query_store as store
import contribution_semantics as semantics
from report_directory import report_directory

ACTBLUE, WINRED = 'C00401224', 'C00694323'
REPORT = 'Telecom Industry Spending Overview.xlsx'
PATTERN = '* Industry Spending Overview*.xlsx'
REAL_GLOB = Path.glob


def telecom_only(self, pattern):
    """Path.glob that lists only the Telecom workbook for the report-folder scan, so the test does not
    hash and parse every other industry's workbook. Any other glob passes through."""
    for item in REAL_GLOB(self, pattern):
        if pattern != PATTERN or item.name == REPORT:
            yield item


class TelecomConduitAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            directory = report_directory()
            if not (directory / REPORT).is_file():
                raise unittest.SkipTest(f'{REPORT} is not in the report directory; nothing to read.')
            with patch.object(Path, 'glob', telecom_only):
                sources = data._sources(directory)
        except (ValueError, OSError) as error:
            raise unittest.SkipTest(f'The Telecom source cannot be resolved here: {error}') from error
        cls.source = next((item for item in sources if item['id'] == 'Telecom'), None)
        if cls.source is None or not cls.source.get('available'):
            raise unittest.SkipTest('The Telecom report is not available with pinned source evidence; nothing to read.')
        cls.path = data.CACHE_ROOT / (data._token(cls.source) + '.sqlite')
        if not cls.path.is_file():
            raise unittest.SkipTest('The Telecom contribution index is not built; this test never builds one.')
        if not query._view_ready(cls.path):
            raise unittest.SkipTest('The Telecom query view is not built; this test never builds one.')

    def ask(self, **request):
        request = {'dataset': 'Telecom', 'basis': 'receipt', 'measures': ['amount'], 'limit': 1000, 'offset': 0,
                   'filters': [{'field': 'cycle', 'op': 'eq', 'value': '2025-2026'}, *request.pop('filters', [])],
                   **request}
        parts = semantics.validate(request)
        # store.cached would write the answer under state\; compute it directly instead.
        with patch.object(store, 'cached', lambda namespace, identity, compute, stable=lambda: True: compute()):
            return query._query_ready(self.path, request, self.source, *parts, hold=False)

    def categories(self):
        result = self.ask(dimensions=['recipient_category'])
        return {row['recipient_category']: row['amount'] for row in result['rows']}, result

    def test_actblue_is_a_conduit_with_its_audited_amount(self):
        result = self.ask(dimensions=['committee_id', 'recipient_category'],
                          filters=[{'field': 'committee_id', 'op': 'eq', 'value': ACTBLUE}])
        self.assertEqual([(r['committee_id'], r['recipient_category'], r['amount']) for r in result['rows']],
                         [(ACTBLUE, 'Conduit', 159480386)])

    def test_winred_is_a_conduit_too(self):
        result = self.ask(dimensions=['committee_id', 'recipient_category'],
                          filters=[{'field': 'committee_id', 'op': 'eq', 'value': WINRED}])
        self.assertEqual([(r['committee_id'], r['recipient_category'], r['amount']) for r in result['rows']],
                         [(WINRED, 'Conduit', 24429223)])

    def test_super_pac_is_the_audited_figure(self):
        groups, _ = self.categories()
        self.assertEqual(groups['Super PAC'], 5020251)
        self.assertEqual(groups['Conduit'], 159480386 + 24429223)

    def test_every_category_still_adds_up_to_the_cent(self):
        groups, result = self.categories()
        self.assertEqual(sum(groups.values()), 725609345)
        self.assertEqual(result['totals']['amount'], 725609345)
        self.assertTrue(result['reconciliation']['conserved'])
        # The categories other than the two the conduits left are exactly what the store always said.
        self.assertEqual({name: value for name, value in groups.items() if name not in ('Conduit', 'Super PAC')},
                         {'Company PAC': 259639815, 'Candidate': 120591436, 'JFC': 75365410, 'Unclassified': 36513120,
                          'Party Committee': 23287639, 'Leadership PAC': 15718353, 'Carey': 3885136,
                          'Trade Association': 1678576})

    def test_the_category_filter_selects_the_same_money_as_the_group(self):
        groups, _ = self.categories()
        for name in ('Conduit', 'Super PAC', 'Carey'):
            with self.subTest(category=name):
                result = self.ask(filters=[{'field': 'recipient_category', 'op': 'eq', 'value': name}])
                self.assertEqual(result['totals']['amount'], groups[name])

    def test_the_stand_in_relations_have_the_columns_of_the_real_tables(self):
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            self.assertEqual(semantics._index_columns(), [row[1] for row in db.execute('PRAGMA table_info(facts)')])
        with closing(store._readonly(store.view(self.path))) as db:
            self.assertEqual(semantics._relation_columns('facts_full'),
                             [row[1] for row in db.execute('PRAGMA table_info(facts_full)')])

    def test_records_of_the_conduit_say_conduit(self):
        table = semantics.table('facts', ['recipient_category', 'committee_id'], {'basis': 'receipt'}, original=True)
        with closing(data._ro(self.path)) as db:
            rows = db.execute('SELECT DISTINCT committee_id,recipient_category FROM ' + table +
                              ' WHERE committee_id IN (?,?) AND cycle=? AND receipt_cents!=0 ORDER BY committee_id',
                              (ACTBLUE, WINRED, '2025-2026')).fetchall()
        self.assertEqual([tuple(row) for row in rows], [(ACTBLUE, 'Conduit'), (WINRED, 'Conduit')])


if __name__ == '__main__':
    unittest.main()
