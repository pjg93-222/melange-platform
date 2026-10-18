"""The FEC bulk file's operating expenditures beside the API spending panel (app/bulk_oppexp.py, D-074).

The reader and the route are tested together: a stub crawler reader (sys.modules) sets each committee's API Schedule B
status, a temporary bulk directory holds tiny Parquet files in the ingest's exact columns, and /api/candidate-spending's
respond() is called as the panel calls it (detail=1). Covers: the file-cycle join and its boundaries, memo rows hidden,
empty and missing files, the label's source date (never a download time), and the move from incomplete to complete API
coverage, at which the bulk rows disappear and the API figures never change because of them.
"""
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'app'))
import bulk_oppexp as bo  # noqa: E402
import candidate_spending as cs  # noqa: E402

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

CAND = 'H0KY06104'
A, B = 'C00000001', 'C00000002'
SCHEMA = pa.schema([('CMTE_ID', pa.string()), ('NAME', pa.string()), ('PURPOSE', pa.string()), ('MEMO_CD', pa.string()),
                    ('SUB_ID', pa.string()), ('transaction_date', pa.date32()), ('amount', pa.decimal128(14, 2)),
                    ('file_cycle', pa.int16()), ('source_kind', pa.string()), ('coverage_class', pa.string())])


def brow(committee, sub, date, amount, name='VENDOR LLC', purpose='CONSULTING', memo='', cycle=2024):
    return {'CMTE_ID': committee, 'NAME': name, 'PURPOSE': purpose, 'MEMO_CD': memo, 'SUB_ID': sub,
            'transaction_date': dt.date.fromisoformat(date), 'amount': Decimal(amount), 'file_cycle': cycle,
            'source_kind': bo.SOURCE_KIND, 'coverage_class': bo.COVERAGE_CLASS}


def write_vintage(root, cycle, vintage_dir, rows, vintage_asof='2026-10-12T17:36:09Z'):
    folder = Path(root) / f'{cycle % 100:02d}' / vintage_dir
    folder.mkdir(parents=True)
    path = folder / f'oppexp{cycle % 100:02d}.parquet'
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path)
    manifest = {'schema_version': 1, 'source_kind': bo.SOURCE_KIND, 'coverage_class': bo.COVERAGE_CLASS,
                'bulk_file': f'oppexp{cycle % 100:02d}.zip', 'file_cycle': cycle,
                'parquet': {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}
    if vintage_asof is not None:
        manifest['vintage_asof'] = vintage_asof
    (folder / 'MANIFEST.json').write_text(json.dumps(manifest), encoding='utf-8')


def api_row(sub, amount, date='2024-03-01'):
    return {'committee_id': A, 'sub_id': sub, 'transaction_id': sub, 'disbursement_date': date, 'disbursement_amount': amount,
            'disbursement_description': 'CONSULTING', 'disbursement_purpose_category': 'OTHER', 'memo_code': None,
            'entity_type': 'ORG', 'recipient_committee_id': None, 'line_number': '17', 'recipient_name': 'API VENDOR'}


def committee(cid, b_status, rows=(), totals_amount=None):
    coverage = 'complete' if b_status == 'complete' else ('partial' if rows else 'pending')
    totals = [{'committee_id': cid, 'cycle': 2024, 'disbursements': totals_amount, 'loan_repayments': 0,
               'transfers_to_other_authorized_committee': 0, 'coverage_end_date': '2024-12-31'}] if totals_amount is not None else []
    return {'committee_id': cid, 'committee_name': f'CAMPAIGN {cid}', 'cycle': 2024, 'coverage_status': coverage,
            'endpoint_status': {'B': b_status, 'totals': 'complete' if totals else 'pending'},
            'retrieved_at': {'B_latest': '2026-10-15T12:00:00Z' if rows else None, 'totals': None}, 'totals': totals,
            'rows': list(rows), 'row_occurrences': len(rows), 'disclosure_boundaries': 0}


class Reader:
    """Stands in for tools/committee_activity_reader.py: answers with the committees set on the class."""
    committees = []
    mapped = True

    @classmethod
    def read_campaign_spending(cls, *, cycle, candidate_id=None, committee_id=None, state_dir=None):
        ids = [item['committee_id'] for item in cls.committees]
        mapping = {'candidate_id': candidate_id, 'source': 'test', 'vintage_or_retrieved_at': None, 'committee_ids': ids} if cls.mapped else None
        return {'query': {'cycle': cycle, 'candidate_id': candidate_id, 'committee_id': committee_id},
                'snapshot': {'batch': 'batch-04', 'sha256': 'f' * 64}, 'mapping': mapping, 'committees': cls.committees}


class BulkWithPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        # oppexp24: committee A has two payments, one memo row, and a 2023-dated payment (still the 2024 file);
        # committee B has one payment. An older vintage of the same file must never be read.
        write_vintage(root, 2024, '20261012', [
            brow(A, '4000000000000000003', '2024-06-30', '250.00', name='PRINT SHOP', purpose='PRINTING'),
            brow(A, '4000000000000000002', '2023-02-01', '1000.50', name='OFFICE LANDLORD', purpose='RENT'),
            brow(A, '4000000000000000001', '2024-06-30', '250.00', name='AMEX', purpose='SEE BELOW', memo='X'),
            brow(B, '4000000000000000009', '2024-01-15', '75.25', name='B VENDOR', purpose='TRAVEL')])
        write_vintage(root, 2024, '20260308', [brow(A, '3999999999999999999', '2024-01-01', '9999.99', name='STALE')],
                      vintage_asof='2026-03-08T21:48:05Z')
        # oppexp22 holds a 2020-dated payment for committee A: it belongs to cycle 2022 by the file, not to 2020 or 2024.
        write_vintage(root, 2022, '20261011', [brow(A, '2000000000000000001', '2020-12-30', '40.00', name='OLD VENDOR', cycle=2022)],
                      vintage_asof='2026-10-11T16:59:07Z')
        self.env = patch.dict(os.environ, {bo.DIR_ENV: root, cs.FIXTURE_ENV: ''})
        self.env.start()
        self.reader = patch.dict(sys.modules, {cs.READER: Reader})
        self.reader.start()
        Reader.mapped = True
        bo._VERIFIED.clear()

    def tearDown(self):
        self.reader.stop()
        self.env.stop()
        self.tmp.cleanup()

    def ask(self, cycle=2024, detail=True):
        query = {'candidate': [CAND], 'cycle': [str(cycle)]}
        if detail:
            query['detail'] = ['1']
        return cs.respond(query)

    # ---- the reader alone
    def test_file_cycle_join_and_boundaries(self):
        got = bo.read_rows([A], 2024)
        self.assertEqual([r['sub_id'] for r in got['rows']], ['4000000000000000003', '4000000000000000002'])
        self.assertIn('2023-02-01', [r['date'] for r in got['rows']], 'an odd-year payment in oppexp24 belongs to 2024')
        old = bo.read_rows([A], 2022)
        self.assertEqual([r['date'] for r in old['rows']], ['2020-12-30'], 'a 2020-dated row in oppexp22 is cycle 2022')
        self.assertNotIn('2020-12-30', [r['date'] for r in got['rows']])
        self.assertEqual(bo.read_rows([A], 2026)['status'], 'unavailable', 'no file on hand is unavailable, never zero rows')
        self.assertNotIn('STALE', [r['payee'] for r in got['rows']], 'the newest vintage only')

    def test_memo_rows_hidden_by_default(self):
        got = bo.read_rows([A], 2024)
        self.assertEqual((got['total'], got['memo_rows_hidden']), (2, 1))
        self.assertNotIn('AMEX', [r['payee'] for r in got['rows']])
        self.assertEqual(bo.read_rows([A], 2024, include_memo=True)['total'], 3)

    def test_simplified_fields_and_exact_amounts(self):
        first = bo.read_rows([A], 2024)['rows'][1]
        self.assertEqual({k: first[k] for k in ('date', 'payee', 'amount', 'purpose')},
                         {'date': '2023-02-01', 'payee': 'OFFICE LANDLORD', 'amount': 1000.5, 'purpose': 'RENT'})

    def test_empty_result_is_empty_not_unavailable(self):
        got = bo.read_rows(['C00000099'], 2024)
        self.assertEqual((got['status'], got['total'], got['rows']), ('empty', 0, []))

    def test_label_uses_recorded_source_date_or_says_unavailable(self):
        self.assertEqual(bo.label('2026-10-12T17:36:09Z'), 'FEC bulk file, as of Oct 12, 2026')
        self.assertEqual(bo.label(None), 'FEC bulk file, source date unavailable')
        write_vintage(self.tmp.name, 2026, '20261011', [brow(A, '5000000000000000001', '2025-05-05', '10.00', cycle=2026)], vintage_asof=None)
        Reader.committees = [committee(A, 'pending')]
        body, status = self.ask(2026)
        self.assertEqual(body['bulk']['label'], 'FEC bulk file, source date unavailable')

    def test_tampered_parquet_is_refused_and_api_answer_survives(self):
        path = Path(self.tmp.name) / '24' / '20261012' / 'oppexp24.parquet'
        path.write_bytes(path.read_bytes() + b'x')
        Reader.committees = [committee(A, 'pending')]
        body, status = self.ask()
        self.assertEqual((status, body['status'], body['bulk']['shown'], body['bulk']['reason']), (404, 'not_collected', False, 'bulk_error'))

    # ---- the route: incomplete -> complete
    def test_transition_from_incomplete_to_complete(self):
        # 1. nothing collected by the API: 404 as before, and the bulk rows ride along, labeled
        Reader.committees = [committee(A, 'pending')]
        body, status = self.ask()
        self.assertEqual((status, body['status']), (404, 'not_collected'))
        self.assertTrue(body['bulk']['shown'])
        self.assertEqual(body['bulk']['label'], 'FEC bulk file, as of Oct 12, 2026')
        self.assertEqual(body['bulk']['coverage_class'], 'bulk_vintage_operating_spend')
        # 2. the API has some pages: the API figures are exactly what they are without any bulk file; bulk sits beside them
        api_rows = [api_row('S1', 500.0), api_row('S2', 125.0)]
        Reader.committees = [committee(A, 'pending', api_rows)]
        partial, status = self.ask()
        self.assertEqual((status, partial['coverage_status']), (200, 'partial'))
        self.assertTrue(partial['bulk']['shown'])
        with patch.dict(os.environ, {bo.DIR_ENV: str(Path(self.tmp.name) / 'nowhere')}):
            alone, _ = self.ask()
        self.assertEqual(alone['bulk']['reason'], 'unavailable')
        for key in ('total_itemized', 'positive_total', 'categories', 'reported_total', 'coverage'):
            self.assertEqual(partial[key], alone[key], f'{key} must not change because of bulk rows')
        self.assertEqual(partial['total_itemized'], 625.0, 'only the API rows are itemized')
        # 3. the API is complete: no bulk row is returned at all, only the one line saying why
        Reader.committees = [committee(A, 'complete', api_rows, totals_amount=625.0)]
        done, status = self.ask()
        self.assertEqual((status, done['coverage_status']), (200, 'complete'))
        self.assertEqual((done['bulk']['shown'], done['bulk']['reason']), (False, 'api_complete'))
        self.assertNotIn('rows', done['bulk'])
        self.assertEqual(done['total_itemized'], 625.0)

    def test_mixed_committees_read_bulk_only_where_api_is_incomplete(self):
        Reader.committees = [committee(A, 'complete', [api_row('S1', 500.0)], totals_amount=500.0), committee(B, 'pending')]
        body, status = self.ask()
        self.assertEqual(body['bulk']['committee_ids'], [B])
        self.assertEqual([r['payee'] for r in body['bulk']['rows']], ['B VENDOR'])

    def test_hover_card_never_reads_bulk(self):
        Reader.committees = [committee(A, 'pending')]
        body, status = self.ask(detail=False)
        self.assertNotIn('bulk', body)

    def test_unmapped_candidate_has_no_bulk(self):
        Reader.mapped = False
        Reader.committees = [{'committee_id': None, 'coverage_status': 'mapping_unknown', 'endpoint_status': {}}]
        body, status = self.ask()
        self.assertEqual(body['status'], 'mapping_unknown')
        self.assertNotIn('bulk', body)


if __name__ == '__main__':
    unittest.main()
