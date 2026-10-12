"""Offline cutoff derivation, receipt persistence, and console integration."""
from contextlib import closing
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import collection_coverage as coverage
import validate_industry_coverage as gate
import server
from test_validate_industry_coverage import create_complete_database, write_receipt


class CoverageDatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'mentat.db'
        self.fanout = self.root / 'fanout.json'
        self.receipt = self.root / 'coverage.json'
        create_complete_database(self.db)
        write_receipt(self.fanout, self.db)
        self.today = date(2026, 9, 10)
        self.assertions = [dict(industry='Fintech', company=c, cycle=y, kind=k,
                                coverage_start=f'{y-1}-01-01',
                                verified_data_through=f'{y}-12-31' if y < 2026 else '2026-08-31',
                                verification_evidence='Dated source coverage statement / fixture')
                           for c in ('Alpha', 'Beta') for y in (2022, 2024, 2026) for k in coverage.KINDS]

    def derive(self, assertions=None, passed=True, cycles=(2022, 2024, 2026)):
        with closing(sqlite3.connect(self.db)) as db:
            grid, _, _ = gate.api_progress_grid(db, ('Alpha', 'Beta'), cycles)
            return coverage.derive(db, 'Fintech', cycles, ('Alpha', 'Beta'), grid, passed,
                                   self.assertions if assertions is None else assertions, self.today)

    def persisted(self, assertions=None):
        write_receipt(self.fanout, self.db, fec_date_evidence=self.assertions if assertions is None else assertions)
        result = gate.validate(self.db, 'Fintech', (2022, 2024, 2026), self.fanout)
        self.receipt.write_text(json.dumps(result), encoding='utf-8')
        return result

    def inspect(self):
        return coverage.inspect(self.db, self.receipt, 'Fintech', [2022, 2024, 2026])

    def test_earliest_evidenced_date_across_all_companies_and_searches(self):
        self.assertions[-1]['verified_data_through'] = '2026-06-30'
        rows = self.derive()
        self.assertEqual([r['verified_data_through'] for r in rows], ['2022-12-31', '2024-12-31', '2026-06-30'])
        self.assertEqual(rows[-1]['freshness'], 'stale')
        self.assertEqual(len(rows[-1]['evidence']), 4)

    def test_missing_one_search_affects_only_its_cycle(self):
        rows = self.derive(self.assertions[:-1])
        self.assertEqual(rows[1]['freshness'], 'current')
        self.assertIsNone(rows[-1]['verified_data_through'])
        self.assertIn('Beta / corporate', rows[-1]['missing_evidence'])

    def test_missing_or_failed_collection_checks_never_verify_dates(self):
        self.assertTrue(all(r['verified_data_through'] is None for r in self.derive(passed=False)))
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE api_work_progress SET stored_complete=0 WHERE company='Beta' AND cycle=2026")
        self.assertIsNone(self.derive()[-1]['verified_data_through'])

    def test_legacy_completion_and_timestamps_do_not_establish_coverage(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('ALTER TABLE api_work_progress ADD COLUMN updated_at TEXT')
            db.execute("UPDATE api_work_progress SET updated_at='2026-09-10T23:00:00Z'")
        self.assertTrue(all(r['verified_data_through'] is None for r in self.derive([])))

    def test_empty_searches_with_explicit_evidence_can_verify(self):
        row = self.derive()[-1]
        self.assertEqual(row['verified_data_through'], '2026-08-31')
        self.assertIsNone(row['latest_observed_transaction_date'])

    def test_invalid_future_and_wrong_cycle_dates_fail_closed(self):
        for value in ('2026-02-30', '2027-01-01', '2026-09-11', '2024-12-31',
                      '2026-08-31T00:00:00Z', '08/31/2026', None, 20260831):
            with self.subTest(value=value):
                rows = [dict(a) for a in self.assertions]
                rows[-1]['verified_data_through'] = value
                self.assertIsNone(self.derive(rows)[-1]['verified_data_through'])

    def test_missing_start_or_evidence_cannot_prove_full_cycle_interval(self):
        for field, value in (('coverage_start', None), ('coverage_start', '2026-01-01'),
                             ('verification_evidence', ''), ('industry', 'Other'), ('company', 'Other')):
            with self.subTest(field=field):
                rows = [dict(a) for a in self.assertions]
                rows[-1][field] = value
                self.assertIsNone(self.derive(rows)[-1]['verified_data_through'])

    def test_duplicate_assertions_do_not_select_newest_date(self):
        older = {**self.assertions[-1], 'verified_data_through': '2026-07-31'}
        self.assertEqual(self.derive([*self.assertions, older])[-1]['verified_data_through'], '2026-07-31')
        self.assertIsNone(self.derive([*self.assertions, {**older, 'verified_data_through': 'bad'}])[-1]['verified_data_through'])

    def test_malformed_scope_identities_do_not_hide_or_supply_evidence(self):
        junk = [None, 'not an assertion'] + [
            {**self.assertions[-1], field: value}
            for field in ('industry', 'company', 'kind') for value in ([], {})]
        self.assertEqual(self.derive([*self.assertions, *junk]), self.derive())
        self.assertIsNone(self.derive([*self.assertions[:-1], *junk])[-1]['verified_data_through'])

    def test_repeated_transaction_dates_still_count_invalid_rows_and_check_each_cycle(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            for table in ('contributions', 'corporate_giving'):
                db.execute(f'ALTER TABLE {table} ADD COLUMN contribution_receipt_date TEXT')
                db.executemany(f'INSERT INTO {table} VALUES (?,?,?,?,?)', [
                    (f'{cycle}-{company}-{value}', 'Fintech', company, cycle, value)
                    for cycle in (2024, 2026) for company in ('Alpha', 'Beta')
                    for value in ('2024-12-31', 'bad-date')])
        rows = self.derive()
        self.assertEqual(rows[1]['latest_observed_transaction_date'], '2024-12-31')
        self.assertEqual(rows[1]['invalid_transaction_dates'], 4)
        self.assertIsNone(rows[2]['latest_observed_transaction_date'])
        self.assertEqual(rows[2]['invalid_transaction_dates'], 8)

    def test_staleness_boundaries_and_historical_cycles(self):
        row = dict(cycle=2026, verified_data_through='2026-08-11')
        self.assertEqual(coverage.freshness(row, self.today)['freshness'], 'current')
        self.assertEqual(coverage.freshness({**row, 'verified_data_through': '2026-08-10'}, self.today)['freshness'], 'stale')
        past = dict(cycle=2024, verified_data_through='2024-12-31')
        self.assertEqual(coverage.freshness(past, self.today)['freshness'], 'current')
        self.assertEqual(coverage.freshness({**past, 'verified_data_through': '2024-12-30'}, self.today)['freshness'], 'stale')

    def test_transaction_maxima_are_separate_and_scoped(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('ALTER TABLE contributions ADD COLUMN contribution_receipt_date TEXT')
            db.execute('ALTER TABLE corporate_giving ADD COLUMN contribution_receipt_date TEXT')
            db.executemany('INSERT INTO contributions VALUES (?,?,?,?,?)', [
                ('1', 'Fintech', 'Alpha', 2026, '2026-08-05T00:00:00'),
                ('2', 'Other', 'Alpha', 2026, '2026-09-09'),
                ('3', 'Fintech', 'Unknown', 2026, '2026-09-09'),
                ('4', 'Fintech', 'Alpha', 2026, 'garbage'),
                ('5', 'Fintech', 'Alpha', 2026, '2027-01-01'),
                ('6', 'Fintech', 'Alpha', 2024, '2024-12-31'),
            ])
            db.execute("INSERT INTO corporate_giving VALUES ('7','Fintech','Beta',2026,'2026-08-15')")
        rows = self.derive([])
        self.assertEqual(rows[-1]['latest_observed_transaction_date'], '2026-08-15')
        self.assertEqual(rows[-1]['invalid_transaction_dates'], 2)
        self.assertIsNone(rows[-1]['verified_data_through'])
        self.assertEqual(rows[1]['latest_observed_transaction_date'], '2024-12-31')

    def test_database_evidence_table_is_supported(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE fec_date_evidence (industry,company,cycle,kind,coverage_start,verified_data_through,verification_evidence)')
            db.executemany('INSERT INTO fec_date_evidence VALUES (?,?,?,?,?,?,?)', [tuple(a.values()) for a in self.assertions])
        self.assertEqual(self.derive([])[-1]['verified_data_through'], '2026-08-31')

    def test_validator_stores_hash_bound_dates_and_console_reads_them(self):
        result = self.persisted()
        stored = result['fec_coverage']
        self.assertEqual(stored['database_sha256'], hashlib.sha256(self.db.read_bytes()).hexdigest())
        self.assertEqual(self.inspect()[-1]['verified_data_through'], '2026-08-31')
        # A restart must read the receipt, not depend on an in-memory derivation.
        coverage._inspect.cache_clear()
        self.assertEqual(self.inspect()[-1]['verified_data_through'], '2026-08-31')

    def test_new_file_mtime_cannot_advance_verified_cutoff(self):
        self.persisted()
        os.utime(self.db, (1900000000, 1900000000))
        self.assertEqual(self.inspect()[-1]['verified_data_through'], '2026-08-31')
        self.assertTrue(self.inspect()[-1]['database_modified_at'].startswith('2030-'))

    def test_tampered_stored_cutoff_or_missing_source_evidence_is_unverifiable(self):
        value = self.persisted()
        value['fec_coverage']['rows'][-1]['verified_data_through'] = '2026-09-01'
        self.receipt.write_text(json.dumps(value))
        self.assertIsNone(self.inspect()[-1]['verified_data_through'])
        value = self.persisted()
        value['fec_coverage']['rows'][-1]['evidence'].pop()
        self.receipt.write_text(json.dumps(value))
        self.assertIsNone(self.inspect()[-1]['verified_data_through'])

    def test_database_change_during_derivation_cannot_bind_new_hash(self):
        before = coverage.file_stamp(self.db)
        rows = self.derive()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE api_work_progress SET stored_complete=0 WHERE cycle=2026")
        value = coverage.persistable(self.db, rows, before)
        self.assertIsNone(value['database_sha256'])
        self.assertTrue(all(r['verified_data_through'] is None for r in value['rows']))

    def test_incomplete_optional_evidence_schema_stays_unverifiable(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE fec_date_evidence (unrelated TEXT)')
        self.assertTrue(all(r['verified_data_through'] is None for r in self.derive([])))

    def test_transient_read_failure_retries_without_file_modification(self):
        self.persisted()
        with patch.object(coverage, '_read_coverage', side_effect=sqlite3.OperationalError('database locked')):
            self.assertIsNone(self.inspect()[-1]['verified_data_through'])
        with patch.object(coverage.time, 'monotonic', return_value=10**15):
            self.assertEqual(self.inspect()[-1]['verified_data_through'], '2026-08-31')

    def test_database_mutation_invalidates_persisted_dates(self):
        self.persisted()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE api_work_progress SET stored_complete=0 WHERE cycle=2026")
        self.assertTrue(all(r['verified_data_through'] is None for r in self.inspect()))
        self.assertIn('changed', self.inspect()[-1]['reason'])

    def test_nonempty_wal_cannot_reuse_main_database_hash(self):
        self.persisted()
        Path(str(self.db) + '-wal').write_bytes(b'pending changes')
        self.assertIsNone(self.inspect()[-1]['verified_data_through'])

    def test_legacy_receipt_inspects_database_without_writing(self):
        self.persisted([])
        value = json.loads(self.receipt.read_text())
        del value['fec_coverage']
        self.receipt.write_text(json.dumps(value))
        before = self.db.read_bytes(), self.receipt.read_bytes()
        self.assertIsNone(self.inspect()[-1]['verified_data_through'])
        self.assertEqual(before, (self.db.read_bytes(), self.receipt.read_bytes()))

    def test_bad_missing_or_wrong_scope_receipts_never_crash_or_verify(self):
        for content in ('null', '[]', '{broken', '{}'):
            self.receipt.write_text(content)
            self.assertTrue(all(r['freshness'] == 'unverifiable' for r in self.inspect()))
        value = self.persisted()
        for field, wrong in (('industry', 'Other'), ('cycles', [2024]), ('database', str(self.root / 'other.db'))):
            self.receipt.write_text(json.dumps({**value, field: wrong}))
            self.assertTrue(all(r['verified_data_through'] is None for r in self.inspect()))
        self.receipt.unlink()
        self.assertIsNone(self.inspect()[-1]['verified_data_through'])
        self.assertIsNone(coverage.inspect(self.root / 'missing.db', self.receipt, 'Fintech', [2026])[0]['verified_data_through'])
        self.assertFalse((self.root / 'missing.db').exists())

    def test_console_loading_catalog_and_snapshot_use_same_dates(self):
        self.persisted()
        status = {'cycles': [2022, 2024, 2026], 'stage': 'completed', 'industries': [
            {'industry': 'Fintech', 'database': str(self.db), 'coverage_receipt': str(self.receipt), 'status': 'succeeded'}]}
        (self.root / 'status.json').write_text(json.dumps(status))
        run = {'id': 'sample', 'name': 'Saved Fintech run', 'directory': self.root, 'state': 'completed', 'created_at': 1}
        with patch.object(server, 'environment_keys', return_value=[]), patch.object(server, 'load_keys', return_value=[]), patch.object(server, 'key_status', return_value={}):
            app = server.Console(self.root / 'console', read_only=True)
            with patch.object(app, 'runs', return_value=[run]):
                summary = app.summary()
                snapshot = app.snapshot('sample')
        self.assertEqual(summary['coverage'], snapshot['fec_coverage'])
        self.assertEqual(summary['coverage'][-1]['verified_data_through'], '2026-08-31')

    def test_latest_failed_run_does_not_inherit_old_verified_coverage(self):
        old = dict(industry='Fintech', cycle=2026, verified_data_through='2026-08-31', freshness='current')
        new = {**old, 'verified_data_through': None, 'freshness': 'unverifiable'}
        app = object.__new__(server.Console)
        with patch.object(app, 'collection_coverage', side_effect=[[new], [old]]):
            self.assertEqual(app.coverage_catalog([{'id':'new'}, {'id':'old'}]), [new])

    def test_superseded_database_is_not_read_but_uncovered_cycles_are(self):
        app = object.__new__(server.Console)
        old = self.root / 'old'
        new = self.root / 'new'
        for directory, cycles in ((old, [2024, 2026]), (new, [2026])):
            directory.mkdir()
            (directory / 'status.json').write_text(json.dumps({'cycles': cycles, 'industries': [
                {'industry': 'Fintech', 'database': str(directory / 'mentat.db')}]}))
        runs = [{'id': d.name, 'name': d.name, 'directory': d} for d in (new, old)]
        with patch.object(server, 'inspect_coverage', side_effect=lambda db, receipt, name, cycles:
                          coverage.unknown(name, cycles, 'Unavailable')) as inspect:
            rows = app.coverage_catalog(runs)
            self.assertEqual([(r['cycle'], r['run_id']) for r in rows], [(2026, 'new'), (2024, 'old')])
            self.assertEqual(inspect.call_count, 2)
            # Once every scope has a newer result, never open the old database.
            inspect.reset_mock()
            (new / 'status.json').write_text((old / 'status.json').read_text())
            rows = app.coverage_catalog(runs)
            self.assertEqual({r['run_id'] for r in rows}, {'new'})
            self.assertEqual(inspect.call_count, 1)


if __name__ == '__main__':
    unittest.main()
