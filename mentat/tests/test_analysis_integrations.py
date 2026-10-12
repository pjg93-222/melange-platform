"""Provenance, identity, time-scope and accounting tests for Insights adapters."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import analysis_integrations as integrations


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.melange = self.root / 'melange'
        self.context = {'inputs_dir': self.inputs, 'melange_root': self.melange,
                        'recipient_cycles': [{'recipient_id': 'C00000001', 'cycle': 2026},
                                             {'recipient_id': 'C00000002', 'cycle': 2026}],
                        'recipient_ids': ['C00000001', 'C00000002'], 'cycles': [2026],
                        'companies': ['Exact Company'], 'source_scope': {'as_of': '2026-06-30'}}
        self.write(self.inputs / 'fec_beneficiary/cn26/cn.txt',
                   'H0MA01001|EXAMPLE, ALICE|DEM|2026|MA|H|01|I|C|C00000001|||||\n'
                   'S0MA00001|EXAMPLE, BOB|REP|2028|MA|S|00|I|C|C00000003|||||\n')
        self.write(self.inputs / 'fec_committee_master/cm26/cm.txt',
                   'C00000001|ALICE CAMPAIGN||||||MA|P|H|DEM|||ORG|H0MA01001\n'
                   'C00000002|JOINT FUNDRAISER||||||MA|J|N||||ORG|\n')
        self.write(self.inputs / 'fec_beneficiary/ccl26/ccl.txt',
                   'H0MA01001|2026|2026|C00000001|H|P|1\n'
                   'H0MA01001|2026|2026|C00000002|N|J|2\n'
                   'S0MA00001|2028|2026|C00000002|N|J|3\n'
                   'C00000002|2026|2026|C00000002|N|U|4\n'
                   'S0MA00001|2024|2024|C00000001|S|P|5\n')
        self.json(self.inputs / 'verified_company_affiliations.json', {'records': [
            {'company': 'Exact Company', 'entity_id': 'C00000009', 'relationship': 'connected_pac',
             'valid_from': '2026-03-01', 'valid_to': '2026-05-31'},
            {'company': 'Exact Company Subsidiary', 'entity_id': 'C00000008', 'relationship': 'connected_pac',
             'valid_from': '2021-01-01'}]})
        self.write(self.melange / 'fec/legislators.csv',
                   'full_name,bioguide_id,fec_ids\nAlice Example,E000001,H0MA01001\nBob Example,E000002,S0MA00001\n')
        self.bill = {'congress': 119, 'display': 'H.R.100', 'title': 'Example Bill',
                     'introduced_date': '2025-02-03', 'sponsor': {'bioguide': 'E000001', 'name': 'Alice Example', 'date': ''},
                     'cosponsors': [{'bioguide': 'E000002', 'name': 'Bob Example', 'date': '2026-05-03',
                                     'withdrawn_date': '2026-07-01'}],
                     'latest_action': {'date': '2026-07-05', 'text': 'Later action'},
                     'areas': ['Finance'], 'updated_at': '2026-09-20T00:00:00Z',
                     'url': 'https://www.congress.gov/bill/119th-congress/house-bill/100'}
        self.json(self.melange / 'bill_repository_live.json', {'bills': {'119:hr:100': self.bill}, 'updated_at': '2026-09-20'})
        self.total = {'candidate_id': 'H0MA01001', 'cycle': 2026, 'name': 'Alice Example',
                      'coverage_start_date': '2025-01-01', 'coverage_end_date': '2026-06-30',
                      'receipts': 120.50, 'disbursements': 75, 'cash_on_hand_end_period': '45.50',
                      'debts_owed_by_committee': None}
        self.payment = {'committee_id': 'C00000009', 'two_year_transaction_period': 2026,
                        'sub_id': '123456', 'disbursement_date': '2026-04-01', 'disbursement_amount': -1.25,
                        'recipient_name': 'Recipient', 'disbursement_description': 'Refund',
                        'pdf_url': 'https://www.fec.gov/docquery/1/?api_key=SECRET'}
        self.install_pages([('/v1/candidates/totals/', [self.total]),
                            ('/v1/schedules/schedule_b/', [self.payment])])

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def json(self, path, payload):
        self.write(path, json.dumps(payload))

    def install_pages(self, pages, imported=False):
        directory = self.inputs / 'analysis_integrations/saved' if imported else self.melange / 'data/money'
        self.manifest_path = directory / ('manifest.json' if imported else 'fec_refresh_current.json')
        self.manifest = {'status': 'PASS', 'checked_at': '2026-09-20T00:00:00Z',
                         'scope': 'Fixture preserved OpenFEC subset', 'sources': []}
        for index, (endpoint, rows) in enumerate(pages):
            path = directory / f'page-{index}.json'
            self.json(path, {'results': rows})
            self.manifest['sources'].append({'url': 'https://api.open.fec.gov' + endpoint + '?api_key=SECRET',
                                             'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        self.json(self.manifest_path, self.manifest)

    def enrich(self, **request):
        return integrations.enrich(request, self.context)

    def test_profiles_exact_cycle_many_links_are_not_money_allocations(self):
        panel = self.enrich(panels=['profiles'])['profiles']
        self.assertEqual(panel['total_rows'], 2)
        joint = next(row for row in panel['rows'] if row['recipient_id'] == 'C00000002')
        self.assertEqual(joint['candidate_ids'], 'H0MA01001, S0MA00001')
        self.assertIn('Joint fundraiser link', joint['candidate_links'])
        self.assertNotIn('C00000002', joint['candidate_ids'])
        primary = next(row for row in panel['rows'] if row['recipient_id'] == 'C00000001')
        self.assertEqual(primary['candidate_ids'], 'H0MA01001')
        self.assertTrue(all('amount' not in row for row in panel['rows']))

    def test_missing_cycle_does_not_fall_back_to_newer_master(self):
        self.context['recipient_cycles'] = [{'recipient_id': 'C00000001', 'cycle': 2024}]
        row = self.enrich(panels=['profiles'])['profiles']['rows'][0]
        self.assertEqual(row['candidate_ids'], '')
        self.assertEqual(row['identity_status'], 'Not in saved cycle master')

    def test_financial_totals_deduplicate_candidate_linked_to_two_recipients(self):
        panel = self.enrich(panels=['spending'])['spending']
        rows = [row for row in panel['rows'] if row['record_kind'] == 'Reported candidate finances']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['receipts'], 12050)
        self.assertEqual(rows[0]['amount'], 7500)
        self.assertEqual(rows[0]['cash'], 4550)
        self.assertIsNone(rows[0]['debt'])
        self.assertIn('not selected-industry', rows[0]['scope'])
        self.assertEqual(next(column['type'] for column in panel['columns'] if column['key'] == 'amount'), 'money')

    def test_connected_company_committee_finances_do_not_require_receipt_recipient_match(self):
        row = {'committee_id': 'C00000009', 'cycle': 2026, 'coverage_start_date': '2026-03-01',
               'coverage_end_date': '2026-04-30', 'receipts': 100, 'disbursements': 60,
               'cash_on_hand_end_period': 40}
        self.install_pages([('/v1/committee/C00000009/totals/', [row])])
        panel = self.enrich(panels=['spending'])['spending']
        self.assertEqual(len(panel['rows']), 1)
        self.assertEqual(panel['rows'][0]['record_kind'], 'Reported committee finances')
        self.assertEqual(panel['rows'][0]['company'], 'Exact Company')
        self.assertEqual(panel['rows'][0]['amount'], 6000)
        self.assertEqual(panel['spending_summaries'], [])
        # The summary covers money before the verified relationship: do not
        # attribute that whole period to the selected company.
        self.install_pages([('/v1/committee/C00000009/totals/', [{**row, 'coverage_start_date': '2026-01-01'}])])
        self.assertEqual(self.enrich(panels=['spending'])['spending']['rows'], [])

    def test_inverted_financial_coverage_cannot_enter_a_cutoff_summary(self):
        self.install_pages([('/v1/candidates/totals/', [{**self.total, 'coverage_start_date': '2026-07-01'}])])
        panel = self.enrich(panels=['spending'])['spending']
        self.assertEqual(panel['rows'], [])
        self.assertTrue(any('coverage dates' in notice for notice in panel['notices']))

    def test_financial_record_kind_and_committee_identity_follow_saved_endpoint(self):
        row = {'committee_id': 'C00000009', 'candidate_id': 'H0MA01001', 'cycle': 2026,
               'coverage_start_date': '2026-03-01', 'coverage_end_date': '2026-04-30',
               'receipts': 100, 'disbursements': 60}
        self.install_pages([('/v1/committee/C00000009/totals/', [row])])
        panel = self.enrich(panels=['spending'])['spending']
        self.assertEqual(panel['rows'][0]['record_kind'], 'Reported committee finances')
        self.assertEqual(panel['rows'][0]['counterparty'], 'C00000009')
        self.install_pages([('/v1/committee/C00000009/totals/', [{**row, 'committee_id': 'C00000001'}])])
        panel = self.enrich(panels=['spending'])['spending']
        self.assertEqual(panel['rows'], [])
        self.assertTrue(any('disagrees' in notice for notice in panel['notices']))
        self.install_pages([('/v1/candidates/totals/', [{**row, 'candidate_id': ''}])])
        panel = self.enrich(panels=['spending'])['spending']
        self.assertEqual(panel['rows'], [])
        self.assertTrue(any('valid candidate ID' in notice for notice in panel['notices']))

    def test_signed_payments_memo_exclusion_and_exact_dated_affiliation(self):
        variants = [self.payment]
        for sid, changes in [('before', {'disbursement_date': '2026-02-01'}),
                              ('after', {'disbursement_date': '2026-06-01'}),
                              ('near', {'committee_id': 'C00000008'}),
                              ('memo', {'memo_code': 'X'}),
                              ('subtotal', {'memoed_subtotal': True})]:
            variants.append({**self.payment, 'sub_id': sid, **changes})
        self.install_pages([('/v1/schedules/schedule_b/', variants)])
        rows = self.enrich(panels=['spending'])['spending']['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['amount'], -125)
        self.assertEqual(rows[0]['company'], 'Exact Company')
        self.assertEqual(rows[0]['source_url'], 'https://www.fec.gov/docquery/1/')

    def test_later_period_finances_and_events_do_not_leak_past_cutoff(self):
        self.total['coverage_end_date'] = '2026-08-31'
        self.payment['disbursement_date'] = '2026-07-01'
        self.install_pages([('/v1/candidates/totals/', [self.total]), ('/v1/schedules/schedule_b/', [self.payment])])
        self.assertEqual(self.enrich(panels=['spending'])['spending']['rows'], [])
        rows = self.enrich(panels=['legislation'])['legislation']['rows']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['latest_action'] == '' and row['withdrawn_date'] == '' for row in rows))

    def test_source_hash_failure_rejects_whole_manifest(self):
        self.manifest['sources'][1]['sha256'] = '0' * 64
        self.json(self.manifest_path, self.manifest)
        panel = self.enrich(panels=['spending'])['spending']
        self.assertEqual(panel['rows'], [])
        self.assertTrue(any('SHA-256' in notice for notice in panel['notices']))

    def test_financial_periods_are_not_added_or_repeated(self):
        prior = {**self.total, 'coverage_end_date': '2026-03-31', 'receipts': 100}
        self.install_pages([('/v1/candidates/totals/', [self.total, prior])])
        rows = self.enrich(panels=['spending'])['spending']['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['receipts'], 12050)
        competing = {**self.total, 'coverage_start_date': '2026-01-01'}
        self.install_pages([('/v1/candidates/totals/', [self.total, competing])])
        panel = self.enrich(panels=['spending'])['spending']
        self.assertEqual(panel['rows'], [])
        self.assertTrue(any('competing period' in notice for notice in panel['notices']))

    def test_source_path_escape_rejected(self):
        outside = self.root / 'outside.json'
        self.json(outside, {'results': [self.total]})
        self.manifest['sources'][0]['path'] = str(outside)
        self.manifest['sources'][0]['sha256'] = hashlib.sha256(outside.read_bytes()).hexdigest()
        self.json(self.manifest_path, self.manifest)
        panel = self.enrich(panels=['spending'])['spending']
        self.assertEqual(panel['rows'], [])
        self.assertTrue(any('approved source root' in notice for notice in panel['notices']))

    def test_http_request_cannot_choose_source_roots_or_network_urls(self):
        normal = self.enrich(panels=['profiles'])
        attack = self.enrich(panels=['profiles'], inputs_dir=str(self.root), melange_root='https://example.com/')
        self.assertEqual(normal, attack)
        self.assertEqual(integrations._url('https://api.open.fec.gov@evil.example/'), '')
        self.assertEqual(integrations._url('javascript:alert(1)'), '')
        self.assertNotIn('SECRET', json.dumps(self.enrich()))

    def test_official_filing_image_link_retains_only_numeric_document_selector(self):
        public = 'https://docquery.fec.gov/cgi-bin/fecimg/?202608189899552325'
        self.install_pages([('/v1/schedules/schedule_b/', [{**self.payment, 'pdf_url': public}])])
        row = self.enrich(panels=['spending'])['spending']['rows'][0]
        self.assertEqual(row['source_url'], public)
        self.assertEqual(integrations._url(public + '&api_key=SECRET'), '')
        self.assertEqual(integrations._url('https://docquery.fec.gov/cgi-bin/fecimg/?api_key=SECRET'), '')
        self.assertEqual(integrations._url('https://api.open.fec.gov/v1/candidates/?api_key=SECRET'),
                         'https://api.open.fec.gov/v1/candidates/')

    def test_conflicting_duplicate_payment_excluded_exact_duplicates_count_once(self):
        self.install_pages([('/v1/schedules/schedule_b/', [self.payment, self.payment])])
        self.assertEqual(len(self.enrich(panels=['spending'])['spending']['rows']), 1)
        self.install_pages([('/v1/schedules/schedule_b/', [self.payment, {**self.payment, 'disbursement_amount': 5}])])
        panel = self.enrich(panels=['spending'])['spending']
        self.assertEqual(panel['rows'], [])
        self.assertTrue(any('conflicting' in notice for notice in panel['notices']))

    def test_legislation_exact_ids_cycle_and_withdrawal_date(self):
        other = copy.deepcopy(self.bill)
        other['congress'] = 118
        self.bill['cosponsors'][0]['withdrawn_date'] = '2026-06-01'
        self.json(self.melange / 'bill_repository_live.json', {'bills': {'current': self.bill, 'prior': other}})
        panel = self.enrich(panels=['legislation'])['legislation']
        self.assertEqual(len(panel['rows']), 2)
        bob = next(row for row in panel['rows'] if row['member'] == 'Bob Example')
        self.assertEqual(bob['withdrawn_date'], '2026-06-01')
        self.assertEqual(bob['congress'], 119)
        self.assertTrue(all('amount' not in row for row in panel['rows']))

    def test_ambiguous_crosswalk_never_matches_by_name(self):
        self.write(self.melange / 'fec/legislators.csv',
                   'full_name,bioguide_id,fec_ids\nAlice Example,E000001,H0MA01001\nDifferent Name,E000003,H0MA01001\n')
        panel = self.enrich(panels=['legislation'])['legislation']
        self.assertEqual(panel['rows'], [])
        self.assertTrue(any('unambiguous' in notice for notice in panel['notices']))

    def test_missing_cosponsor_date_does_not_become_introduction_date(self):
        self.bill['cosponsors'][0]['date'] = ''
        self.json(self.melange / 'bill_repository_live.json', {'bills': {'current': self.bill}})
        panel = self.enrich(panels=['legislation'])['legislation']
        self.assertEqual(len(panel['rows']), 1)
        self.assertEqual(panel['rows'][0]['relationship'], 'Sponsor')
        self.assertTrue(any('joined date' in notice for notice in panel['notices']))

    def test_fingerprints_invalidate_on_identity_bill_and_saved_page_changes(self):
        previous = integrations.sources_fingerprint(self.context)
        self.bill['title'] = 'Changed title'
        self.json(self.melange / 'bill_repository_live.json', {'bills': {'current': self.bill}})
        changed = integrations.sources_fingerprint(self.context)
        self.assertNotEqual(previous, changed)
        self.json(self.manifest_path.parent / 'page-0.json', {'results': []})
        self.assertNotEqual(changed, integrations.sources_fingerprint(self.context))

    def test_import_adapter_uses_relative_saved_paths_without_melange(self):
        self.manifest_path.unlink()
        self.install_pages([('/v1/candidates/totals/', [self.total])], imported=True)
        rows = self.enrich(panels=['spending'])['spending']['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['receipts'], 12050)

    def test_disabled_integrations_do_no_file_reads(self):
        with patch.object(integrations, '_roots', side_effect=AssertionError('must not read')):
            self.assertEqual(self.enrich(panels=['favorites']), {})
        with patch.object(integrations, '_saved_pages', side_effect=AssertionError('must not read finances')):
            self.assertEqual(set(self.enrich(panels=['profiles'])), {'profiles'})

    def test_missing_sources_have_concrete_unavailable_state(self):
        self.manifest_path.unlink()
        (self.melange / 'bill_repository_live.json').unlink()
        results = self.enrich()
        self.assertEqual(results['spending']['status'], 'unavailable')
        self.assertTrue(any('verified saved OpenFEC' in notice for notice in results['spending']['notices']))
        self.assertEqual(results['legislation']['status'], 'unavailable')
        self.assertTrue(any('FEC-to-Bioguide' in notice for notice in results['legislation']['notices']))

    def test_outgoing_patterns_use_every_payment_before_ledger_cap(self):
        payments = [{**self.payment, 'sub_id': 'a', 'disbursement_amount': 100,
                     'recipient_name': 'First name', 'recipient_committee_id': 'C00000020', 'disbursement_description': 'Contribution'},
                    {**self.payment, 'sub_id': 'b', 'disbursement_amount': 50,
                     'recipient_name': 'Other payee', 'recipient_committee_id': 'C00000021', 'disbursement_description': 'Contribution'},
                    {**self.payment, 'sub_id': 'c', 'disbursement_amount': -20,
                     'recipient_name': 'Same ID renamed', 'recipient_committee_id': 'C00000020', 'disbursement_description': 'Refund'}]
        self.install_pages([('/v1/schedules/schedule_b/', payments), ('/v1/candidates/totals/', [self.total])])
        with patch.object(integrations, 'MAX_ROWS', 1):
            panel = self.enrich(panels=['spending'], top_n=1)['spending']
        self.assertEqual(len(panel['rows']), 1)
        self.assertEqual(panel['total_rows'], 4)
        summary = panel['spending_summaries'][0]
        self.assertEqual(summary['records'], 3)
        self.assertEqual(summary['gross_cents'], 15000)
        self.assertEqual(summary['adjustment_cents'], -2000)
        self.assertEqual(summary['amount_cents'], 13000)
        self.assertEqual(summary['payee_groups'], 2)
        self.assertAlmostEqual(summary['top_payee_share'], 2 / 3, places=7)
        self.assertEqual(summary['top_five_share'], 1)
        top = panel['vendor_patterns'][0]
        self.assertEqual(top['payee_id'], 'C00000020')
        self.assertEqual(top['records'], 2)
        self.assertEqual(top['amount_cents'], 8000)
        self.assertAlmostEqual(top['positive_share'], 2 / 3, places=7)
        self.assertEqual(panel['vendor_patterns_total_rows'], 2)
        self.assertEqual(panel['purpose_patterns_total_rows'], 2)

    def test_pattern_denominators_separate_senders_cycles_and_attribution_scopes(self):
        def payment(sender, cycle, scope, amount, payee_id=''):
            return {'record_kind': 'Observed Schedule B payment', 'entity': sender, 'cycle': cycle,
                    'scope': scope, 'company': 'Company' if scope == 'Dated connected company PAC' else '',
                    'amount': amount, 'counterparty': 'Same reported name', 'counterparty_id': payee_id,
                    'purpose': 'Purpose'}
        rows = [payment('C00000001', 2026, 'Dated connected company PAC', 100),
                payment('C00000001', 2026, 'Selected committee outgoing activity', 200),
                payment('C00000001', 2024, 'Dated connected company PAC', 300),
                payment('C00000002', 2026, 'Dated connected company PAC', -50),
                {'record_kind': 'Reported candidate finances', 'amount': 500000}]
        patterns = integrations._outgoing_patterns(rows)
        self.assertEqual(len(patterns['spending_summaries']), 4)
        self.assertEqual(sum(row['amount_cents'] for row in patterns['spending_summaries']), 550)
        self.assertEqual(sum(row['records'] for row in patterns['vendor_patterns']), 4)
        self.assertEqual(sum(row['amount_cents'] for row in patterns['purpose_patterns']), 550)
        refund_only = next(row for row in patterns['vendor_patterns'] if row['sender_id'] == 'C00000002')
        self.assertIsNone(refund_only['positive_share'])
        self.assertEqual(refund_only['identity_basis'], 'Exact reported name; unresolved ID')

    def test_same_reported_payee_name_with_distinct_ids_remains_separate(self):
        rows = [{'record_kind': 'Observed Schedule B payment', 'entity': 'C00000009', 'cycle': 2026,
                 'scope': 'Dated connected company PAC', 'company': 'Company', 'amount': 100,
                 'counterparty': 'Same name', 'counterparty_id': identifier, 'purpose': ''}
                for identifier in ['C00000001', 'C00000002', '']]
        patterns = integrations._outgoing_patterns(rows)
        self.assertEqual(len(patterns['vendor_patterns']), 3)
        self.assertEqual(len(patterns['purpose_patterns']), 1)
        self.assertEqual(patterns['spending_summaries'][0]['payee_groups'], 3)


if __name__ == '__main__':
    unittest.main()
