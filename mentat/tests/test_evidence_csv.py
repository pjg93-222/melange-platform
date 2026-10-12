"""CSV observations stay local, source-bound, and separated by recorded channel."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import evidence_csv as csv_data
import server as app


class EvidenceCSVTests(unittest.TestCase):
    def document(self, text):
        result = csv_data.parse('observations.csv', text)
        result['id'] = 'csv-' + '1' * 32
        return result

    def receipts(self, rows=1):
        return 'committee_id,committee_name,contribution_receipt_amount,contribution_receipt_date,cycle\n' + ''.join(
            'C00000001,Example PAC,10.25,2026-02-03,2026\n' for _ in range(rows))

    def test_utf8_quotes_refunds_and_duplicates_preserve_exact_source(self):
        text = '\ufeffCMTE_ID,Committee Name,Contribution Receipt Amount,Contribution Receipt Date,Contributor Name,Cycle\n' + (
            'C00000001,Example PAC,10.25,02/03/2026,"Example, Café",2026\n' * 2) + (
            'C00000001,Example PAC,-5,2026-02-04,Refund,2026\n'
            'C00000002,Another PAC,20,2026-02-05,Other,2024\n')
        document = self.document(text)
        view = csv_data.select(document, 'C00000001', '2026')
        self.assertEqual(document['text'], text)
        self.assertEqual(view['source']['sha256'], hashlib.sha256(text.encode('utf-8')).hexdigest())
        self.assertEqual(view['total_rows'], 3)
        self.assertEqual(view['totals'], [{'channel':'Receipts', 'amount':15.5, 'row_count':3}])
        self.assertEqual(view['rows'][0]['raw']['Contributor Name'], 'Example, Café')
        self.assertEqual(view['rows'][0]['date'], '2026-02-03')
        self.assertEqual(view['cycles'], ['2026', '2024'])

    def test_receipts_disbursements_support_and_opposition_are_separate(self):
        text = ('committee_id,contribution_receipt_amount,contribution_receipt_date,disbursement_amount,disbursement_date,expenditure_amount,expenditure_date,support_oppose\n'
                'C00000001,10,2026-01-01,,,,,\n'
                'C00000001,,,20,2026-01-02,,,\n'
                'C00000001,,,,,30,2026-01-03,S\n'
                'C00000001,,,,,40,2026-01-04,O\n')
        view = csv_data.select(self.document(text))
        self.assertEqual({row['channel']:row['amount'] for row in view['totals']}, {
            'Receipts':10, 'Disbursements':20, 'Independent spending · support':30, 'Independent spending · oppose':40})
        self.assertNotIn('total_amount', view)
        self.assertTrue(all(not row['cycle'] for row in view['rows']))

    def test_explicit_mentat_cycle_aggregate_has_no_invented_transaction_date(self):
        document = self.document('Cycle,Source Committee ID,Candidate ID,Candidate/Officeholder,Support/Oppose,Net Included Amount\n'
                                 '2025-2026,C00000001,H0CA00001,Example Candidate,Support,123.45\n')
        row = csv_data.select(document)['rows'][0]
        self.assertEqual(row['status'], 'supported')
        self.assertEqual(row['basis'], 'cycle aggregate')
        self.assertIsNone(row['date'])
        self.assertEqual(row['cycle'], '2026')
        self.assertEqual(row['candidate_name'], 'Example Candidate')

    def test_unsupported_dates_types_and_numbers_remain_visible_without_totals(self):
        text = ('Committee ID,Type,Amount,Date\n'
                'C00000001,receipt,25,not-a-date\n'
                'C00000001,unknown,25,2026-01-01\n'
                'C00000001,receipt,(-25),2026-01-01\n'
                'C00000001,receipt,($-25),2026-01-01\n'
                'C00000001,receipt,NaN,2026-01-01\n'
                'C00000001,receipt,1e308,2026-01-01\n')
        view = csv_data.select(self.document(text))
        self.assertEqual(view['unsupported_rows'], 6)
        self.assertEqual(view['totals'], [])
        self.assertEqual(len(view['rows']), 6)
        json.dumps(view, allow_nan=False)

    def test_semicolon_csv_parses_accounting_amounts_without_double_signs(self):
        view = csv_data.select(self.document('CMTE_ID;Type;Amount;Date\nC00000001;receipt;($1,234.50);01022026\n'))
        self.assertEqual(view['totals'][0]['amount'], -1234.5)
        self.assertEqual(view['rows'][0]['date'], '2026-01-02')

    def test_unassigned_and_extra_cells_remain_inspectable(self):
        view = csv_data.select(self.document('Committee ID,Type,Amount,Date\n'
                                            'C00000001,receipt,10,2026-01-01\n'
                                            'bad-id,receipt,12,2026-01-01,extra\n'), 'unassigned')
        self.assertEqual(view['total_rows'], 1)
        self.assertEqual(view['rows'][0]['raw']['Committee ID'], 'bad-id')
        self.assertEqual(view['rows'][0]['extra_cells'], ['extra'])
        self.assertEqual(view['totals'], [])

    def test_conflicting_amount_types_are_not_chosen_automatically(self):
        view = csv_data.select(self.document('committee_id,contribution_receipt_amount,disbursement_amount,date\n'
                                            'C00000001,10,20,2026-01-01\n'))
        self.assertEqual(view['unsupported_rows'], 1)
        self.assertEqual(view['totals'], [])

    def test_pagination_does_not_truncate_selected_totals(self):
        view = csv_data.select(self.document(self.receipts(251)), offset=200, limit=50)
        self.assertEqual(len(view['rows']), 50)
        self.assertEqual(view['total_rows'], 251)
        self.assertTrue(view['has_more'])
        self.assertEqual(view['totals'][0]['amount'], 2572.75)
        self.assertEqual(view['totals'][0]['row_count'], 251)

    def test_listing_carries_each_committees_lead_figure_by_cycle(self):
        # Console redesign build 06, fix round 1 (R06-02): the picker rail ranks
        # an import's PACs by the figure each pane leads with. The listing
        # carries it, over all cycles and per cycle, and it equals the lead
        # channel of the pane's own summary for the same scope.
        text = ('committee_id,committee_name,contribution_receipt_amount,contribution_receipt_date,'
                'disbursement_amount,disbursement_date,cycle\n'
                'C00000001,Alpha PAC,10.25,2026-01-01,,,2026\n'
                'C00000001,Alpha PAC,5,2024-01-01,,,2024\n'
                'C00000001,Alpha PAC,,,40,2024-02-01,2024\n'
                'C00000002,Beta PAC,7,2026-01-02,,,2026\n'
                'C00000003,Gamma PAC,not a number,2026-01-03,,,2026\n')
        with tempfile.TemporaryDirectory() as temporary:
            store = csv_data.Store(Path(temporary), evict=False)
            job = store.wait(store.ingest_text('observations.csv', text)['id'])
            self.assertEqual(job['state'], 'ready', job.get('message'))
            listed = {item['id']:item for item in store.sources()[0]['committees']}
            lead = lambda totals: max(sorted((row['channel'], row['amount']) for row in totals), key=lambda item: item[1])
            for committee in ('C00000001', 'C00000002'):
                with self.subTest(committee=committee):
                    item = listed[committee]
                    self.assertEqual((item['receipts_channel'], item['receipts']),
                                     lead(store.summary(job['id'], committee)['totals']))
                    for cycle, amount in item['receipts_by_cycle'].items():
                        self.assertEqual(amount, lead(store.summary(job['id'], committee, cycle)['totals'])[1])
            # Alpha leads with 40 of disbursements over all cycles and in 2024,
            # and with its 10.25 of receipts in 2026.
            self.assertEqual(listed['C00000001']['receipts'], 40)
            self.assertEqual(listed['C00000001']['receipts_by_cycle'], {'2024':40, '2026':10.25})
            self.assertEqual(listed['C00000002']['receipts_by_cycle'], {'2026':7})
            # No supported row, no figure: the pane shows a dash, the rail too.
            self.assertIsNone(listed['C00000003']['receipts'])
            self.assertEqual(listed['C00000003']['receipts_by_cycle'], {})

    def test_malformed_or_unidentified_files_are_rejected(self):
        for text in ('', 'Name,Amount\nExample,10\n', 'Committee ID,committee_id\nC00000001,C00000001\n',
                     'Committee ID,Amount\nmissing,10\n', 'committee_id,amount\nC00000001,"broken\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.document(text)
        with self.assertRaises(ValueError):
            csv_data.parse('file.xlsx', self.receipts())
        with patch.object(csv_data, 'MAX_ROWS', 1):
            with self.assertRaises(ValueError):
                self.document(self.receipts(2))

    def test_csv_http_is_authenticated_and_available_in_read_only_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(app, 'report_directory', return_value=root), patch.object(app, 'environment_keys', return_value=[]):
                console = app.Console(root / 'state', read_only=True)
            server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
            server.app = console
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            origin = f'http://127.0.0.1:{server.server_port}'
            payload = json.dumps({'name':'example.csv', 'text':self.receipts(3)}).encode()
            headers = {'Content-Type':'application/json', 'X-Mentat-Token':console.token}
            try:
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(origin + '/api/evidence-csv', data=payload))
                self.assertEqual(error.exception.code, 403)
                with urlopen(Request(origin + '/api/evidence-csv', data=payload, headers=headers)) as response:
                    source = json.load(response)
                with urlopen(origin + '/api/evidence-csv') as response:
                    self.assertEqual(json.load(response)['sources'][0]['id'], source['id'])
                with urlopen(origin + '/api/evidence-csv?id=' + source['id'] + '&committee=C00000001&cycle=2026&offset=1&limit=1') as response:
                    view = json.load(response)
                self.assertEqual(len(view['rows']), 1)
                self.assertEqual(view['total_rows'], 3)
                self.assertEqual(view['totals'][0]['amount'], 30.75)
                self.assertTrue((console.root / 'evidence-csv' / source['id'] / 'import.json').is_file())
                with patch.object(app, 'report_directory', return_value=root), patch.object(app, 'environment_keys', return_value=[]):
                    restarted = app.Console(console.root, read_only=True)
                self.assertEqual(restarted.evidence_csv_view()['sources'][0]['id'], source['id'])
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == '__main__':
    unittest.main()
