"""FEC filing links for imported workbook rows (Paul's ruling 2026-09-24).

A Mentat Overview workbook's cycle sheets carry each transaction's FEC sub_id
("Source ID") but not its filing link. fec_citation resolves the sub_id in the
database the workbook was built from (pinned by the workbook's sha256), falls
back to the industry's repair database only for a sub_id the pinned one does
not hold, and accepts a hit only when committee and amount agree with the row.
ActBlue and WinRed rows link the whole FEC report, because FEC never rendered
their page images.

Fixture-only: every test writes its own data root and its own source database,
and the report folder is replaced by a stub, so nothing here reads the real
report directory or the real repair databases.

Run from mentat/tests: python test_fec_citation.py
"""
from __future__ import annotations

from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))

import evidence_csv  # noqa: E402
import fec_citation  # noqa: E402

IMAGE = 'https://docquery.fec.gov/cgi-bin/fecimg/?202301319575502599'
CONDUIT_IMAGE = 'https://docquery.fec.gov/cgi-bin/fecimg/?202212089553501562'
REPORT = 'https://docquery.fec.gov/cgi-bin/forms/C00401224/1671120/'

# The workbook's own header names (the Overview cycle sheets), cut to what matters here.
IMPORT = '\n'.join([
    'Source ID,Cycle,Contributor,Amount,Committee ID,Recipient,Date,Record Type,Source Table',
    '4020820231723392929,2021-2022,"GIVENS, DAVID",20,C00004812,MORPAC,2023-01-15,CONTRIBUTION,contributions',
    '4099999999999999991,2021-2022,"WRONG, AMOUNT",40,C00004812,MORPAC,2022-10-01,CONTRIBUTION,contributions',
    '4099999999999999992,2021-2022,"WRONG, COMMITTEE",30,C00004812,MORPAC,2022-10-02,CONTRIBUTION,contributions',
    '4099999999999999993,2021-2022,"ONLY, IN FALLBACK",10,C00004812,MORPAC,2022-10-03,CONTRIBUTION,contributions',
    '4010420231660204345,2021-2022,"SHOYKHET, ALEXANDER",25,C00401224,ACTBLUE,2022-11-28,CONTRIBUTION,contributions',
    '4010420231660204399,2021-2022,"NO, FILE NUMBER",15,C00401224,ACTBLUE,2022-11-29,CONTRIBUTION,contributions',
    '']) + '\n'

PINNED_ROWS = [
    # sub_id, committee, amount, date, file_num, pdf_url
    ('4020820231723392929', 'C00004812', 20.0, '2023-01-15', 1685294, IMAGE),
    ('4099999999999999991', 'C00004812', 99.0, '2022-10-01', 1685294, IMAGE),        # amount disagrees
    ('4099999999999999992', 'C00999999', 30.0, '2022-10-02', 1685294, IMAGE),        # committee disagrees
    ('4010420231660204345', 'C00401224', 25.0, '2022-11-28', 1671120, CONDUIT_IMAGE),
    ('4010420231660204399', 'C00401224', 15.0, '2022-11-29', None, CONDUIT_IMAGE),   # no file number
]
FALLBACK_ROWS = [
    ('4099999999999999993', 'C00004812', 10.0, '2022-10-03', 1685294, IMAGE),
    # a conflicting identity is never "rescued" by the second database
    ('4099999999999999991', 'C00004812', 40.0, '2022-10-01', 1685294, IMAGE),
]


def write_database(path, rows, table='contributions'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        for name in ('contributions', 'corporate_giving'):
            db.execute(f'CREATE TABLE {name}(sub_id TEXT UNIQUE, committee_id TEXT, '
                       'contribution_receipt_amount REAL, contribution_receipt_date TEXT, file_num INTEGER, '
                       'pdf_url TEXT, industry TEXT)')
        db.executemany(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?)', [(*row, 'Community Banks') for row in rows])
        db.commit()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pinned = self.root / 'report-db' / 'mentat.db'
        write_database(self.pinned, PINNED_ROWS)
        self.cache = self.root / 'cache'
        write_database(self.cache / 'receipt-metadata-repair-20260910' / 'community_banks' / 'mentat.db',
                       FALLBACK_ROWS)
        fec_citation.clear()
        self.store = evidence_csv.Store(self.root / 'data', evict=False)
        job = self.store.wait(self.store.ingest_text('Community Banks Overview rows.csv', IMPORT)['id'])
        self.assertEqual(job['state'], 'ready', job.get('message'))
        self.identifier = job['id']
        self.record = self.store.record(self.identifier)
        self.sources = [{'id': 'Community Banks', 'industry': 'Community Banks', 'available': True,
                         'workbook_sha256': self.record['sha256'], 'database': self.pinned}]
        for patcher in (patch.object(fec_citation, 'pinned_sources', side_effect=lambda: self.sources),
                        patch.object(fec_citation, '_report_signature', return_value=('stub',)),
                        patch.object(fec_citation, 'CACHE_ROOT', self.cache)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(fec_citation.clear)

    def rows(self, committee):
        page = self.store.select(self.identifier, committee, '2022')
        return {row['counterparty']: row for row in page['rows']}


class Links(unittest.TestCase):
    def test_a_pac_transaction_links_its_page_image(self):
        link = fec_citation.filing_link('C00004812', 1685294, IMAGE)
        self.assertEqual((link['url'], link['kind']), (IMAGE, 'image'))

    def test_a_conduit_transaction_links_its_report_not_the_image(self):
        for committee in ('C00401224', 'c00694323'):
            link = fec_citation.filing_link(committee, 1671120, CONDUIT_IMAGE)
            self.assertEqual(link['kind'], 'report')
            self.assertEqual(link['url'], f'https://docquery.fec.gov/cgi-bin/forms/{committee.upper()}/1671120/')
            self.assertEqual((link['text'], link['title']), ('FEC report', 'FEC has no page image for this filing'))
        # No file number: its image is known not to open, so there is no link at all.
        self.assertIsNone(fec_citation.filing_link('C00401224', None, CONDUIT_IMAGE))
        self.assertIsNone(fec_citation.filing_link('C00401224', '0', CONDUIT_IMAGE))

    def test_nothing_but_a_docquery_image_is_a_page_link(self):
        for url in ('', 'https://www.fec.gov/files/bulk-downloads/2022/pas222.zip',
                    'https://docquery.fec.gov/cgi-bin/fecimg/?202301319575502599&x=1', 'javascript:alert(1)'):
            self.assertIsNone(fec_citation.filing_link('C00004812', 1685294, url), url)

    def test_the_spend_pointer_names_the_committee_page(self):
        pointer = fec_citation.workbook_pointer('c00004812')
        self.assertEqual(pointer['url'], 'https://www.fec.gov/data/committee/C00004812/')
        self.assertEqual((pointer['note'], pointer['text']), ('Filing links are not in this workbook', 'Committee on FEC'))
        self.assertIsNone(fec_citation.workbook_pointer('unassigned'))

    def test_cents_ignore_float_noise_but_not_money(self):
        self.assertEqual(fec_citation.cents(208.33), fec_citation.cents(Decimal('208.32999999999998')))
        self.assertNotEqual(fec_citation.cents('208.33'), fec_citation.cents('208.34'))
        self.assertIsNone(fec_citation.cents(None))


class ImportedRows(Fixture):
    def test_a_resolved_row_carries_its_filing_link(self):
        row = self.rows('C00004812')['GIVENS, DAVID']
        self.assertEqual(row['source_url'], IMAGE)
        self.assertEqual(row['record_id'], '4020820231723392929')
        self.assertEqual(row['fec_link']['kind'], 'image')
        self.assertEqual(row['fec_link']['file_num'], '1685294')
        self.assertEqual(row['fec_link']['source'], 'pinned')
        json.dumps(row)  # the row still serialises for the page

    def test_an_actblue_row_links_its_fec_report(self):
        row = self.rows('C00401224')['SHOYKHET, ALEXANDER']
        self.assertEqual(row['source_url'], REPORT)
        self.assertEqual(row['fec_link']['kind'], 'report')
        self.assertEqual(row['fec_link']['title'], 'FEC has no page image for this filing')
        self.assertNotIn('fecimg', json.dumps(row['fec_link']))

    def test_the_identity_check_rejects_a_disagreeing_hit(self):
        rows = self.rows('C00004812')
        # Same page, same database: the row whose committee and amount agree links...
        self.assertEqual(rows['GIVENS, DAVID']['source_url'], IMAGE)
        # ...and the rows whose database identity disagrees do not.
        for name in ('WRONG, AMOUNT', 'WRONG, COMMITTEE'):
            self.assertNotIn('fec_link', rows[name], name)
            self.assertEqual(rows[name]['source_url'], '', name)
        # An ActBlue row the database gives no file number stays unlinked too.
        self.assertNotIn('fec_link', self.rows('C00401224')['NO, FILE NUMBER'])

    def test_only_a_missing_sub_id_is_asked_of_the_repair_database(self):
        rows = self.rows('C00004812')
        found = rows['ONLY, IN FALLBACK']
        self.assertEqual(found['fec_link']['source'], 'fallback')
        self.assertEqual(found['source_url'], IMAGE)
        # WRONG, AMOUNT agrees with the fallback database, but the pinned one
        # holds that sub_id with another amount: a conflict, not a miss.
        self.assertNotIn('fec_link', rows['WRONG, AMOUNT'])

    def test_no_pinned_workbook_means_fallback_rows_only_and_never_a_guess(self):
        self.sources = []   # the import matches no published workbook
        fec_citation.clear()
        headers = self.record['columns']
        columns = [evidence_csv.key(item) for item in headers]
        page = self.store.select(self.identifier, 'C00004812', '2022')
        rows = [evidence_csv.rebuild(headers, columns, row['row_number'], list(row['raw'].values()))
                for row in page['rows']]
        stats = fec_citation.attach(self.record, rows, columns, 'Community Banks')
        linked = {row['counterparty']: row['fec_link']['source'] for row in rows if 'fec_link' in row}
        # Only the repair database answers, and only for the row it holds with an agreeing identity.
        self.assertEqual(linked, {'ONLY, IN FALLBACK': 'fallback', 'WRONG, AMOUNT': 'fallback'})
        self.assertEqual(stats['misses'], {'not_found': 2})
        # With no industry there is no repair database either: nothing is linked.
        fec_citation.clear()
        rows = [evidence_csv.rebuild(headers, columns, row['row_number'], list(row['raw'].values()))
                for row in page['rows']]
        self.assertEqual(fec_citation.attach(self.record, rows, columns, '')['linked'], 0)

    def test_the_lookup_is_memoised_and_single_flight(self):
        calls = []

        def slow_sources():
            calls.append(1)
            threading.Event().wait(0.2)
            return self.sources
        fec_citation.clear()
        with patch.object(fec_citation, 'pinned_sources', side_effect=slow_sources):
            threads = [threading.Thread(target=self.rows, args=('C00004812',)) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
            self.rows('C00401224')
        self.assertEqual(len(calls), 1, 'the pin is resolved once, not once per request')

    def test_a_row_that_resolves_nowhere_is_left_exactly_as_rebuild_made_it(self):
        headers = self.record['columns']
        columns = [evidence_csv.key(item) for item in headers]
        cells = ['4099999999999999992', '2021-2022', 'WRONG, COMMITTEE', '30', 'C00004812', 'MORPAC',
                 '2022-10-02', 'CONTRIBUTION', 'contributions']
        before = evidence_csv.rebuild(headers, columns, 4, cells)
        after = evidence_csv.rebuild(headers, columns, 4, cells)
        stats = fec_citation.attach(self.record, [after], columns, 'Community Banks')
        self.assertEqual(stats['misses'], {'identity_mismatch': 1})
        self.assertEqual(before, after)


# S3: "Spend in <place>" rows of an imported workbook (evidence_geo.import_spent).
# The fixture data root, boundaries and snapshot are test_evidence_geo's own.
import evidence_geo  # noqa: E402
from test_evidence_geo import GeoFixture  # noqa: E402

SPEND = {'cycle': '2024', 'committee': 'C00804179', 'candidate_id': 'H0CA48198',
         'candidate': 'STEEL, MICHELLE', 'direction': 'Support', 'amount': Decimal(4000)}


class SpendPointer(GeoFixture):
    def spend(self, activity):
        store, identifier = self.store()
        with patch('graph_data.report_activity', return_value=activity), patch(
                'downstream.inventory', return_value={}):
            return evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'spent',
                                          store=store, snapshot_root=self.snapshot_root)

    def test_a_workbook_without_filing_records_points_spend_rows_at_the_committee(self):
        payload = self.spend([{**SPEND, 'filings': []}])
        self.assertEqual(payload['filing_fallback'], fec_citation.workbook_pointer('C00804179'))
        self.assertEqual(payload['filing_fallback']['url'], 'https://www.fec.gov/data/committee/C00804179/')
        recipient = payload['kinds'][0]['districts'][0]['recipients'][0]
        self.assertEqual(recipient['filings'], [])   # no citation is invented for the row itself
        self.assertTrue(payload['reconciles'])

    def test_the_pointer_is_only_for_a_workbook_that_carries_no_filing_links(self):
        self.assertIn('filing_fallback', self.spend([{**SPEND, 'filings': []}]))
        records = [{'amount': 4000, 'source_url': IMAGE}]
        cited = self.spend([{**SPEND, 'filings': records}])
        self.assertNotIn('filing_fallback', cited)
        self.assertEqual(cited['kinds'][0]['districts'][0]['recipients'][0]['filings'], records)
        # A saved snapshot's spend map and an import's raised map never carry it.
        self.assertNotIn('filing_fallback', self.snapshot_spend())
        store, identifier = self.store()
        raised = evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'raised', store=store)
        self.assertNotIn('filing_fallback', raised)


if __name__ == '__main__':
    unittest.main()
