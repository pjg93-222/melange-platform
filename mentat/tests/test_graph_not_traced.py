"""F4 (Paul approved 2026-09-25): Graph says when money is not traced.

Three gaps, each with its negative control:
  * Estimates with no audit bundle say "No estimate bundle for this industry yet;
    rebuild the workbook", not an empty panel.
  * A cycle whose transfer index F2 named stale, missing or unresolved shows
    "Not traced: the transfer index for <cycle> is <status>", with the rebuild
    command for a tooltip, and never an estimate (so never $0).
  * A committee with no saved snapshot is followed only through its workbook's
    Transfer Paths sheet; with no sheet, or with a stale index, the drawer says
    "Transfers from this committee are not traced: ...".
The Transfer Paths flag is server.report_quality's, read from the sheet index.
"""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import graph_data  # noqa: E402
from graph_data import graph_view  # noqa: E402
from report_insights import digest, stamp  # noqa: E402
import server as app  # noqa: E402
from graph_fixture import add_graph_sheets  # noqa: E402

COMMAND = 'python "C:/Mentat/tools/fec_transfer_graph.py" --directory "C:/Mentat/inputs" --cycles {cycle}'
NO_SHEET = ('Transfers from this committee are not traced: no saved network, '
            'and this workbook has no Transfer Paths sheet.')


def bundle(path, statuses, rows=()):
    """<workbook>.audit as enrich_downstream.py --audit-only writes it (F2 keys when `statuses` is not None)."""
    audit = path.with_suffix('.audit')
    audit.mkdir(exist_ok=True)
    csv = audit / 'allocation_estimates.csv'
    csv.write_text('cycle,root,candidate_id,candidate_name,direction,estimated_amount\n'
                   + ''.join(f'{year},C00000003,H0CA00001,Candidate One,Support,{value}\n' for year, value in rows),
                   encoding='utf-8')
    manifest = {'version': 1, 'workbook_name': path.name, 'workbook_sha256': digest(path),
                'csv_sha256': {csv.name: digest(csv)}}
    if statuses is not None:
        manifest['transfer_index_status'] = statuses
        manifest['transfer_index_notes'] = {
            year: f'Not traced: the transfer index for {year} is {status}; rebuild with {COMMAND.format(cycle=year)}'
            for year, status in statuses.items() if status != 'current'}
    (audit / 'audit-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')


def transfer_paths(book, rows=()):
    sheet = book.create_sheet('Transfer Paths')
    for row in [['Routes'], [], [f'{len(rows)} records; {len(rows)} shown. Complete destination data.'], [],
                ['Cycle', 'Source Committee', 'Destination Committee', 'Reported Net Transfer', 'Traversal Status'],
                *rows]:
        sheet.append(row)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'Giving.xlsx'
        self.build()

    def build(self, sheets=False, cycles=('2025-2026',)):
        book = openpyxl.Workbook()
        add_graph_sheets(book)
        for extra in cycles[1:]:
            book.copy_worksheet(book['2025-2026']).title = extra
        if sheets:
            transfer_paths(book)
        book.save(self.path)
        book.close()

    def view(self, **filters):
        return graph_view([self.path], filters)


class EstimateBundle(Fixture):
    def test_no_bundle_names_what_is_missing_and_what_makes_it(self):
        value = self.view(mode='estimates')
        self.assertFalse(value['candidate_available'])
        self.assertEqual(value['candidates'], [])
        self.assertEqual(value['candidate_note'], 'No estimate bundle for this industry yet; rebuild the workbook.')
        self.assertEqual(value['candidate_note'], graph_data.NO_ESTIMATE_BUNDLE)
        self.assertNotIn('Errno', value['candidate_note'])
        self.assertEqual((value['transfer_index'], value['candidate_note_title']), ([], ''))

    def test_a_stale_cycle_says_not_traced_and_shows_no_estimate_not_even_zero(self):
        # The pre-ruling export wrote 0.00 for an untraced cycle; even that row is not shown.
        bundle(self.path, {'2026': 'stale'}, rows=[('2026', 0), ('2026', 55)])
        value = self.view(mode='estimates')
        self.assertFalse(value['candidate_available'])
        self.assertEqual(value['candidates'], [])
        self.assertEqual(value['candidate_details'], [])
        self.assertEqual(value['candidate_note'], 'Not traced: the transfer index for 2026 is stale.')
        self.assertEqual(value['candidate_note_title'], 'Rebuild with: ' + COMMAND.format(cycle='2026'))
        self.assertEqual(value['transfer_index'], [{'cycle': '2026', 'status': 'stale',
                                                     'note': 'Not traced: the transfer index for 2026 is stale',
                                                     'command': COMMAND.format(cycle='2026')}])

    def test_every_untraced_status_is_named(self):
        for status in ('missing', 'unresolved'):
            with self.subTest(status=status):
                bundle(self.path, {'2026': status}, rows=[('2026', 55)])
                value = self.view(mode='estimates')
                self.assertFalse(value['candidate_available'])
                self.assertEqual(value['candidate_note'], f'Not traced: the transfer index for 2026 is {status}.')

    def test_negative_control_a_current_index_shows_its_estimates_and_no_note(self):
        bundle(self.path, {'2026': 'current'}, rows=[('2026', 123)])
        value = self.view(mode='estimates')
        self.assertTrue(value['candidate_available'], value['candidate_note'])
        self.assertEqual([row['amount'] for row in value['candidates']], [123])
        self.assertNotIn('Not traced', value['candidate_note'])
        self.assertEqual(value['candidate_note_title'], '')
        self.assertEqual(value['transfer_index'][0]['note'], '')

    def test_a_bundle_written_before_the_status_existed_claims_nothing(self):
        bundle(self.path, None, rows=[('2026', 123)])
        value = self.view(mode='estimates')
        self.assertTrue(value['candidate_available'])
        self.assertEqual(value['transfer_index'], [])
        self.assertNotIn('Not traced', value['candidate_note'])

    def test_one_untraced_cycle_among_two_is_named_and_the_traced_one_still_shows(self):
        self.build(cycles=('2025-2026', '2023-2024'))
        bundle(self.path, {'2024': 'missing', '2026': 'current'}, rows=[('2024', 50), ('2026', 123)])
        both = self.view(mode='estimates')
        self.assertTrue(both['candidate_available'], both['candidate_note'])
        self.assertEqual([row['amount'] for row in both['candidates']], [123])
        self.assertIn('Not traced: the transfer index for 2024 is missing.', both['candidate_note'])
        self.assertEqual(both['candidate_note_title'], 'Rebuild with: ' + COMMAND.format(cycle='2024'))
        stale = self.view(mode='estimates', cycle='2024')
        self.assertFalse(stale['candidate_available'])
        self.assertEqual(stale['candidate_note'], 'Not traced: the transfer index for 2024 is missing.')
        current = self.view(mode='estimates', cycle='2026')
        self.assertTrue(current['candidate_available'])
        self.assertNotIn('Not traced', current['candidate_note'])


class ActivityTrace(Fixture):
    def activity(self, units=None, **filters):
        with patch('downstream.inventory', return_value=units or {}):
            return self.view(mode='activity', **filters)

    def test_no_snapshot_and_no_transfer_paths_says_this_committee_is_not_traced(self):
        value = self.activity(recipient='C00000003')
        trace = value['transfer_trace']
        self.assertFalse(trace['root_traced'])
        self.assertEqual(trace['note'], NO_SHEET)
        self.assertTrue(value['candidate_note'].endswith(NO_SHEET), value['candidate_note'])
        self.assertEqual([(row['cycle'], row['committee'], row['root']) for row in trace['committees']],
                         [('2026', 'C00000003', True)])
        # Its own saved activity still shows; only its transfers are untraced.
        self.assertTrue(value['candidate_available'])
        self.assertEqual(value['candidates'][0]['amount'], 777)

    def test_negative_control_a_transfer_paths_sheet_traces_it(self):
        self.build(sheets=True)
        value = self.activity(recipient='C00000003')
        self.assertTrue(value['transfer_trace']['root_traced'])
        self.assertEqual(value['transfer_trace']['note'], '')
        self.assertNotIn('not traced', value['candidate_note'])

    def test_a_stale_index_leaves_the_transfer_paths_sheet_empty_and_says_so(self):
        self.build(sheets=True)
        bundle(self.path, {'2026': 'stale'})
        value = self.activity(recipient='C00000003')
        self.assertFalse(value['transfer_trace']['root_traced'])
        self.assertEqual(value['transfer_trace']['note'],
                         'Transfers from this committee are not traced: no saved network, '
                         'and the transfer index for 2026 is stale.')
        self.assertEqual(value['candidate_note_title'], 'Rebuild with: ' + COMMAND.format(cycle='2026'))
        bundle(self.path, {'2026': 'current'})
        self.assertEqual(self.activity(recipient='C00000003')['transfer_trace']['note'], '')

    def test_a_saved_root_names_the_committees_past_it_that_are_not_traced(self):
        unit = {'committee_id': 'C00000003', 'cycle': '2026', 'receipts': [], 'expenditures': [],
                'disbursements': [{'line_number': '22', 'disbursement_amount': 100, 'disbursement_type': '24G',
                                   'recipient_committee_id': 'C00000009', 'sub_id': 'd1'}],
                'totals': {'disbursements': 100.0}, '_source': 'fixture', '_sha256': 'f' * 64}
        value = self.activity(units={('2026', 'C00000003'): unit}, recipient='C00000003')
        trace = value['transfer_trace']
        self.assertTrue(trace['root_traced'])
        self.assertEqual(trace['note'], 'Transfers from 1 other committee in this network are not traced: '
                                        'no saved network, and this workbook has no Transfer Paths sheet.')
        self.assertEqual([(row['committee'], row['root']) for row in trace['committees']], [('C00000009', False)])

    def test_the_whole_industry_counts_the_funded_committees(self):
        value = self.activity()
        self.assertEqual(value['transfer_trace']['note'],
                         'Transfers from 6 of the 6 funded committees are not traced: no saved network, '
                         'and this workbook has no Transfer Paths sheet.')

    def test_an_unavailable_drawer_still_says_what_was_not_traced(self):
        value = self.activity(recipient='C00000004')
        self.assertFalse(value['candidate_available'])
        self.assertIn('Missing evidence is not zero spending.', value['candidate_note'])
        self.assertTrue(value['candidate_note'].endswith(NO_SHEET), value['candidate_note'])

    def test_direct_answers_carry_no_trace_fields(self):
        self.assertNotIn('transfer_trace', self.view())

    def test_the_flag_is_report_quality_s_transfer_paths_flag(self):
        for sheets in (False, True):
            with self.subTest(sheets=sheets):
                self.build(sheets=sheets)
                self.assertEqual(graph_data.has_transfer_paths(stamp(self.path)),
                                 app.report_quality(self.path)['transfer_paths'])
                self.assertEqual(graph_data.has_transfer_paths(stamp(self.path)), sheets)


if __name__ == '__main__':
    unittest.main()
