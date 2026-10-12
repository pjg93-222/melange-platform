"""Proof-bound Insights adapter: scope, snapshots, conservation and invalidation."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import analysis_data as a
import analysis_history as h
import test_contribution_data as legacy


class AnalysisAdapterTests(unittest.TestCase):
    def setUp(self):
        legacy.ContributionQueryTests.setUp(self)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE facts SET committee_id='C00000001',recipient='Example Committee',funding_source='employee_original'")
            db.execute("UPDATE facts SET date='2026-10-01',cycle='2025-2026',year='2026' WHERE source_id='b'")
            db.commit()
        self.token = 'fixture-token'
        self.signature = 'integration-v1'
        self.enrich_calls = []
        def enrich(request, context):
            self.enrich_calls.append(copy.deepcopy(context))
            return {key: {'status': 'unavailable', 'title': key, 'rows': [], 'columns': [], 'notices': ['Fixture has no saved external evidence.']}
                    for key in ('profiles', 'spending', 'legislation')}
        patches = [patch.object(h, 'ROOT', Path(self.tmp.name) / 'history'),
                   patch.object(a.store, 'ROOT', Path(self.tmp.name) / 'query-cache'),
                   patch.object(a.data, '_source', return_value=self.source),
                   patch.object(a.data, '_ready', return_value=(self.path, None)),
                   patch.object(a.data, '_token', side_effect=lambda source: self.token),
                   patch.object(a.data, '_provenance', return_value={}),
                   patch.object(a.integrations, 'sources_fingerprint', side_effect=lambda ctx: self.signature),
                   patch.object(a.integrations, 'enrich', side_effect=enrich)]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        a._jobs.clear()
        self.before = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def ready(self, **kwargs):
        request = {'dataset': 'Example', 'as_of': '2026-09-20', **kwargs}
        for _ in range(200):
            result = a.query(self.tmp.name, request)
            if result['status'] == 'ready':
                return result
            self.assertEqual(result['status'], 'preparing')
            time.sleep(.01)
        self.fail('Background Insights did not finish.')

    def test_full_scope_exact_money_options_and_local_journal(self):
        result = self.ready()
        self.assertEqual(set(result['panels']), {'favorites', 'trends', 'changes'})
        row = result['panels']['favorites']['rows'][0]
        self.assertEqual(row['amount_cents'], 5000)
        self.assertEqual(row['gross_cents'], 10000)
        self.assertEqual(result['options']['cycles'], ['2023-2024', '2025-2026'])
        self.assertEqual(result['scope']['baseline_cycle'], '2023-2024')
        self.assertEqual(result['history']['observations_inspected'], 3)
        self.assertEqual(result['history']['newly_inspected_versions'], 3)
        self.assertEqual(result['source_history'][0]['index_token'], self.token)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.before)

    def test_narrow_insights_scan_preserves_every_field_and_rejects_modified_cache(self):
        scan=a.analysis_scan
        fields=','.join('"'+f+'"' for f in scan.FIELDS)
        def rows(path):
            with closing(sqlite3.connect(path)) as db:
                return db.execute('SELECT '+fields+' FROM facts ORDER BY source_id').fetchall()
        expected=rows(self.path)
        projected=scan.view(self.path)
        self.assertEqual(rows(projected),expected)
        with closing(sqlite3.connect(projected)) as db:
            db.execute('UPDATE facts SET receipt_cents=999999');db.commit()
        self.assertEqual(rows(scan.view(self.path)),expected)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.before)

    def test_default_panels_do_not_open_unselected_integrations(self):
        with patch.object(a.integrations,'sources_fingerprint',side_effect=AssertionError('Unselected source opened')):
            result=self.ready()
        self.assertEqual(set(result['panels']),{'favorites','trends','changes'})
        self.assertEqual(self.enrich_calls,[])

    def test_panel_toggle_reuses_scope_but_not_wrong_integration_inputs(self):
        self.ready()
        result = self.ready(panels=['profiles', 'forecast'])
        self.assertEqual(set(result['panels']), {'profiles', 'forecast'})
        self.assertEqual(len(self.enrich_calls), 1)
        self.assertEqual(self.enrich_calls[0]['recipient_cycles'], [{'recipient_id': 'C00000001', 'cycle': 2024}])
        self.assertEqual(result['panels']['forecast']['status'], 'unavailable')
        self.signature = 'integration-v2'
        self.ready(panels=['profiles'])
        self.assertEqual(len(self.enrich_calls), 2)

    def test_explicit_automatic_periods_and_engine_panel_toggles_reuse_one_job(self):
        with patch.object(a, '_prepare', wraps=a._prepare) as prepare:
            automatic = self.ready()
            scope = automatic['scope']
            self.assertTrue(scope['current_cycle'])
            explicit = self.ready(baseline_cycle=scope['baseline_cycle'] or None, current_cycle=scope['current_cycle'])
            toggled = self.ready(panels=['favorites', 'trends', 'changes', 'concentration'])
            self.assertEqual(prepare.call_count, 1, 'Swap, a re-picked automatic period or an engine toggle must not rerun the scan')
        self.assertEqual(explicit['panels'], automatic['panels'])
        self.assertEqual(set(toggled['panels']), {'favorites', 'trends', 'changes', 'concentration'})
        self.assertEqual(toggled['scope']['panels'], ['favorites', 'trends', 'changes', 'concentration'])
        self.assertEqual(self.enrich_calls, [])

    def test_verified_disk_cache_survives_background_job_and_memory_eviction(self):
        before = self.ready()
        a._jobs.clear()
        with a.store._mutex:
            a.store._memory.clear()
            a.store._memory_bytes = 0
        after = self.ready()
        self.assertEqual(before, after)
        self.assertEqual(len(self.enrich_calls), 0)
        self.assertTrue(list((a.store.ROOT / 'responses' / 'insights').glob('*.json')))

    def test_records_cutoff_pagination_snapshot_and_history(self):
        self.ready()
        request = {'dataset': 'Example', 'as_of': '2026-09-20', 'index_token': self.token, 'limit': 1}
        first = a.records(self.tmp.name, request)
        second = a.records(self.tmp.name, {**request, 'offset': 1})
        self.assertEqual(first['total_records'], 2)
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        self.assertEqual(sum(r['amount_cents'] for r in first['rows'] + second['rows']), 5000)
        self.assertTrue(first['rows'][0]['first_seen_by_insights'])
        self.assertNotIn('contributor_key', first['rows'][0])
        self.token = 'changed-token'
        with self.assertRaisesRegex(ValueError, 'source changed'):
            a.records(self.tmp.name, request)

    def test_company_scope_cannot_leak_other_company_records(self):
        result = a.records(self.tmp.name, {'dataset': 'Example', 'as_of': '2026-09-20', 'company': 'B'})
        self.assertEqual(result['total_records'], 0)
        self.assertEqual(result['rows'], [])

    def test_baseline_before_current_and_available_by_as_of(self):
        old = self.ready(as_of='2024-09-20')
        self.assertEqual(old['scope']['current_cycle'], '2023-2024')
        with self.assertRaisesRegex(ValueError, 'at or before'):
            self.ready(as_of='2024-09-20', current_cycle='2025-2026')

    def test_unknown_client_paths_coverage_and_bad_filters_refused(self):
        for extra in ({'melange_root': 'elsewhere'}, {'complete_cycles': [2022, 2024]}, {'path': 'private'},
                      {'basis': 'raw'}, {'panels': ['lobbying']}, {'as_of': '2026-02-30'}, {'top_n': True},
                      {'comparison_dataset': 'Example'}, {'company': ['A']},
                      {'baseline_cycle': '2025-2026', 'current_cycle': '2023-2024'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                a.validate({'dataset': 'Example', **extra})
        for extra in ({'filters': [{'field': 'source_path', 'op': 'eq', 'value': 'x'}]},
                      {'filters': [{'field': 'amount', 'op': 'eq', 'value': 1.5}]}, {'limit': 5001}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                a.validate({'dataset': 'Example', **extra}, records=True)

    def test_unstable_index_is_refused(self):
        Path(str(self.path) + '-wal').write_bytes(b'active')
        with self.assertRaisesRegex(ValueError, 'active writes'):
            a.query(self.tmp.name, {'dataset': 'Example'})

    def test_capacity_refusal_keeps_narrowing_options_and_rolls_back_history(self):
        with patch.object(a.metrics, 'MAX_COMPACT_GROUPS', 1):
            result = self.ready(panels=a.PANEL_IDS)
        self.assertFalse(result['metadata']['complete_input_scan'])
        self.assertEqual(result['options']['companies'], ['A'])
        self.assertTrue(all(p['status'] == 'unavailable' and not p['rows'] for p in result['panels'].values()))
        self.assertEqual(result['source_history'], [])
        self.assertIsNone(result['history'])
        self.assertEqual(self.enrich_calls, [])

    def test_context_keeps_selected_company_without_eligible_receipts(self):
        self.ready(company='Company with saved PAC evidence', panels=['spending'])
        self.assertEqual(self.enrich_calls[0]['companies'], ['Company with saved PAC evidence'])
        self.assertEqual(self.enrich_calls[0]['cycles'], [2024, 2026])
        self.assertEqual(self.enrich_calls[0]['recipient_ids'], [])

    def test_integration_display_limit_preserves_full_source_count(self):
        panel = {'status': 'ready', 'title': 'Profiles', 'columns': [], 'notices': [],
                 'rows': [{'recipient': 'One'}, {'recipient': 'Two'}], 'total_rows': 2}
        with patch.object(a.integrations, 'enrich', return_value={'profiles': panel}):
            result = self.ready(top_n=1, panels=['profiles'])
        self.assertEqual(len(result['panels']['profiles']['rows']), 1)
        self.assertEqual(result['panels']['profiles']['total_rows'], 2)
        self.assertTrue(result['panels']['profiles']['truncated'])

    def test_latest_baseline_cannot_resolve_to_same_implicit_current_cycle(self):
        with self.assertRaisesRegex(ValueError, 'baseline.*earlier'):
            self.ready(baseline_cycle='2025-2026')

    def test_invalid_filter_shapes_and_offset_bounds_are_validation_errors(self):
        cases = [
            {'filters': [{'field': [], 'op': 'eq', 'value': 'A'}]},
            {'filters': [{'field': {}, 'op': 'eq', 'value': 'A'}]},
            {'offset': 1 << 100}, {'as_of': False}, {'as_of': 0},
            {'filters': [{'field': 'amount', 'op': 'eq', 'value': 1 << 100}]},
            {'filters': [{'field': 'company', 'op': 'eq', 'value': float('nan')}]},
            {'filters': [{'field': 'company', 'op': 'in', 'value': [float('inf')]}]},
        ]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                a.validate({'dataset': 'Example', **extra}, records=True)

    def test_cycle_conflict_does_not_supply_a_false_integration_link(self):
        self.seed_drill_facts([
            {'amount_cents': 100, 'date': '2024-06-10', 'cycle': '2021-2022'},
            {'amount_cents': 200, 'date': '2024-06-11', 'committee_id': 'C00000002'},
        ])
        self.ready(as_of='2024-09-20', panels=['profiles'])
        self.assertEqual(self.enrich_calls[0]['recipient_cycles'], [{'recipient_id': 'C00000002', 'cycle': 2024}])

    def test_literal_unknown_and_blank_groups_have_complete_source_filters(self):
        self.seed_drill_facts([
            {'amount_cents': 100, 'company': '', 'funding_source': ''},
            {'amount_cents': 200, 'company': 'Unknown', 'funding_source': 'Unknown'},
            {'amount_cents': 300, 'committee_id': '', 'recipient': ''},
            {'amount_cents': 400, 'committee_id': '', 'recipient': 'Unknown'},
            {'amount_cents': 500, 'company': 'Unknown', 'cycle': '2021-2022', 'date': '2022-03-01'},
        ])
        result = self.ready(as_of='2024-09-20', panels=['favorites', 'concentration', 'changes'], top_n=100)
        for key in ('favorites', 'concentration'):
            for row in result['panels'][key]['rows']:
                with self.subTest(panel=key, row=row):
                    records = self.drill_records(row['filters'])
                    measure = 'gross_cents' if key == 'concentration' else 'amount_cents'
                    self.assertEqual(sum(record['amount_cents'] for record in records), row[measure])
        for row in result['panels']['changes']['rows']:
            with self.subTest(panel='changes', row=row):
                before = sum(item['amount_cents'] for item in self.drill_records(row['comparison_scopes']['baseline']))
                after = sum(item['amount_cents'] for item in self.drill_records(row['comparison_scopes']['current']))
                self.assertEqual(after - before, row['change_cents'])

    def test_invalid_cycle_never_emits_an_unrestricted_cycle_drill(self):
        self.seed_drill_facts([
            {'amount_cents': 100},
            {'amount_cents': 200, 'cycle': 'not-an-election-cycle'},
        ])
        result = self.ready(as_of='2024-09-20', panels=['trends', 'favorites'])
        unknown = next(row for row in result['panels']['trends']['rows'] if row['cycle_end'] is None)
        self.assertEqual(unknown['amount_cents'], 200)
        self.assertIsNone(unknown['filters'])
        self.assertIsNone(unknown['matched_filters'])
        favorite = result['panels']['favorites']['rows'][0]
        self.assertEqual(sum(row['amount_cents'] for row in self.drill_records(favorite['filters'])), 300)

    def seed_drill_facts(self, records):
        """Replace only the isolated fixture with representative accounting facts."""
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            prototype = dict(db.execute('SELECT * FROM facts LIMIT 1').fetchone())
            db.execute('DELETE FROM facts')
            for index, overrides in enumerate(records):
                row = {**prototype, 'source_id': 'drill-' + str(index), 'company': 'A',
                       'committee_id': 'C00000001', 'recipient': 'Recipient One',
                       'funding_source': 'employee_original', 'cycle': '2023-2024',
                       'date': '2024-02-01', 'year': '2024', 'contributor_key': 'donor-' + str(index),
                       **overrides}
                amount = row.pop('amount_cents', 100)
                for field in ('raw_cents', 'receipt_cents', 'original_cents', 'supported_cents', 'pac_cents'):
                    row[field] = amount
                db.execute('INSERT INTO facts (' + ','.join('"' + key + '"' for key in row) +
                           ') VALUES (' + ','.join('?' for _ in row) + ')', list(row.values()))
            db.commit()
        a._jobs.clear()

    def drill_records(self, filters, **scope):
        result = a.records(self.tmp.name, {'dataset': 'Example', 'as_of': '2024-09-20',
                                          'limit': 5000, 'filters': filters, **scope})
        self.assertFalse(result['has_more'])
        return result['rows']

    def test_every_analytic_drill_reconciles_with_signed_and_missing_identities(self):
        self.seed_drill_facts([
            {'amount_cents': 100, 'cycle': '2021-2022', 'date': '2022-02-01'},
            {'amount_cents': 200, 'cycle': '2021-2022', 'date': '2022-12-01'},
            {'amount_cents': 300}, {'amount_cents': -50, 'date': '2024-03-01'},
            {'amount_cents': 80, 'company': 'B', 'committee_id': 'C00000002', 'cycle': '2021-2022', 'date': '2022-03-01'},
            {'amount_cents': 120, 'company': 'B', 'committee_id': 'C00000002', 'date': '2024-04-01'},
            {'amount_cents': 40, 'committee_id': 'C00000002', 'date': '2024-04-15'},
            {'amount_cents': 90, 'company': 'C', 'committee_id': 'C00000003', 'date': '2024-05-01'},
            {'amount_cents': 500, 'funding_source': 'pac_activity'},
            {'amount_cents': 1000, 'date': '2024-12-01'},
            {'amount_cents': 777, 'date': '2024-06-99'},
            {'amount_cents': 333, 'cycle': '2021-2022', 'date': '2024-02-11'},
            {'amount_cents': 25, 'company': ''},
            {'amount_cents': 35, 'committee_id': '', 'recipient': ''},
            {'amount_cents': 45, 'funding_source': ''},
            {'amount_cents': 11, 'company': 'With trailing space ', 'committee_id': '', 'recipient': 'Reported name '},
        ])
        result = self.ready(as_of='2024-09-20', panels=list(a.metrics.PANEL_ORDER), top_n=100)
        for key in ('favorites', 'trends', 'timing'):
            for row in result['panels'][key]['rows']:
                with self.subTest(panel=key, row=row):
                    records = self.drill_records(row['filters'])
                    self.assertEqual(sum(record['amount_cents'] for record in records), row['amount_cents'])
                    if key == 'trends':
                        matched = self.drill_records(row['matched_filters'])
                        self.assertEqual(sum(record['amount_cents'] for record in matched), row['matched_amount_cents'])
                    self.assertNotIn('drill-9', [record['source_id'] for record in records])
        for row in result['panels']['concentration']['rows']:
            with self.subTest(panel='concentration', row=row):
                records = self.drill_records(row['filters'])
                self.assertTrue(all(record['amount_cents'] > 0 for record in records))
                self.assertEqual(sum(record['amount_cents'] for record in records), row['gross_cents'])
        for key in ('changes', 'relationships'):
            for row in result['panels'][key]['rows']:
                with self.subTest(panel=key, row=row):
                    baseline = sum(record['amount_cents'] for record in self.drill_records(row['comparison_scopes']['baseline']))
                    current = sum(record['amount_cents'] for record in self.drill_records(row['comparison_scopes']['current']))
                    self.assertEqual(current - baseline, row['change_cents'])
                    if key == 'relationships':
                        self.assertEqual((baseline, current), (row['baseline_cents'], row['current_cents']))
        for row in result['panels']['overlap']['rows']:
            with self.subTest(panel='overlap', row=row):
                records = self.drill_records(row['filters'])
                self.assertTrue(all(record['amount_cents'] > 0 and record['committee_id'] for record in records))
                first = sum(record['amount_cents'] for record in records if record['company'] == row['company'])
                second = sum(record['amount_cents'] for record in records if record['company'] == row['other_company'])
                self.assertEqual((first, second), (row['company_positive_cents'], row['other_positive_cents']))
        self.assertEqual(result['panels']['forecast']['status'], 'unavailable')
        opened = 0
        for row in result['panels']['changes']['recipient_components']:
            if not row['comparison_scopes']:
                continue
            with self.subTest(panel='changes.recipient_components', row=row):
                baseline = sum(record['amount_cents'] for record in self.drill_records(row['comparison_scopes']['baseline']))
                current = sum(record['amount_cents'] for record in self.drill_records(row['comparison_scopes']['current']))
                self.assertEqual(current - baseline, row['change_cents'])
                opened += 1
        self.assertGreater(opened, 0)
        for row in result['panels']['favorites']['current_period_leaders']:
            with self.subTest(panel='favorites.current_period_leaders', row=row):
                baseline = sum(record['amount_cents'] for record in self.drill_records(row['comparison_scopes']['baseline']))
                current = sum(record['amount_cents'] for record in self.drill_records(row['filters']))
                self.assertEqual((baseline, current), (row['baseline_cents'], row['current_cents']))

    def test_temporal_drills_exclude_invalid_dates_and_cycle_conflicts(self):
        self.seed_drill_facts([
            {'amount_cents': 100, 'date': '2024-06-10'},
            {'amount_cents': 200, 'date': '2024-06-99'},
            {'amount_cents': 300, 'date': '2024-06-11', 'cycle': '2021-2022'},
        ])
        result = self.ready(as_of='2024-09-20', panels=['favorites', 'trends', 'timing'])
        favorite = result['panels']['favorites']['rows'][0]
        self.assertEqual(sum(row['amount_cents'] for row in self.drill_records(favorite['filters'])), 600)
        timing = result['panels']['timing']['rows'][0]
        self.assertEqual(timing['amount_cents'], 100)
        self.assertEqual(sum(row['amount_cents'] for row in self.drill_records(timing['filters'])), 100)
        trend = next(row for row in result['panels']['trends']['rows'] if row['cycle_end'] == 2024)
        self.assertEqual(sum(row['amount_cents'] for row in self.drill_records(trend['filters'])), 300)
        self.assertEqual(sum(row['amount_cents'] for row in self.drill_records(trend['matched_filters'])), 100)

    def test_forecast_historical_basis_drills_use_exact_cycle_and_quarter(self):
        facts = []
        for cycle in (2018, 2020, 2022, 2024):
            facts.extend([{'amount_cents': 100, 'cycle': f'{cycle - 1}-{cycle}', 'date': f'{cycle}-02-01'},
                          {'amount_cents': 200, 'cycle': f'{cycle - 1}-{cycle}', 'date': f'{cycle}-11-01'}])
        self.seed_drill_facts(facts)
        rows = list(a._rows(self.path, a.validate({'dataset': 'Example', 'as_of': '2026-09-20'})))
        forecast = a.metrics.analyze(rows, {'as_of': '2026-09-20', 'panels': ['forecast']},
                                    {'complete_cycles': [2018, 2020, 2022, 2024]})['panels']['forecast']
        self.assertEqual(forecast['status'], 'ready')
        for row in forecast['rows']:
            with self.subTest(product=row['product'], period=row['period']):
                records = self.drill_records(row['filters'], as_of='2026-09-20')
                self.assertEqual(sum(record['amount_cents'] for record in records), row['amount_cents'])
        self.assertEqual(next(row for row in forecast['rows'] if row['product'] == 'Next-quarter baseline')['amount_cents'], 200)


if __name__ == '__main__':
    unittest.main()
