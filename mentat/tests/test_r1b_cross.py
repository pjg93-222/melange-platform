"""Inert R1b cross-component controls: D071 concurrency/recovery x D072 campaign spending x D073 schema 3, end to end through main().

N=4 on schema 3 with totals enabled plus a priority list; a hard interruption and its recovery (totals pairs are path-templated, so their
orphans must still match their endpoint) and the reader on the recovered ledger; candidate-committee lookup accounting and recovery; and a
migrated REAL accepted batch-02 copy opened by this collector with a zero-attempt run (skipped when the snapshot is absent).
No credentials, provider or network.
"""
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import threading
import unittest
from unittest import mock

import test_collect_committee_activity_concurrency as cc

driver, accounted = cc.driver, cc.accounted
contracts = sys.modules['spending_endpoint_contracts']
TOTALS_ID = contracts.binding('totals')['contract_id']
LOOKUP_ID = contracts.binding('candidate_committees')['contract_id']
LOOKED_UP = cc.cid(2)
REAL_STATE = Path(os.environ.get('MENTAT_REAL_COMMITTEE_STATE') or Path(__file__).resolve().parents[1] / 'state' / 'committee-activity')
REAL_SNAPSHOT = REAL_STATE / 'accepted-batches' / 'batch-02' / 'frontier.sqlite'


class CampaignWorld(cc.World):
    """The B/E world plus a totals row per committee and a one-committee candidate lookup; pure functions of (endpoint, query)."""
    def __init__(self, fail_lookup=False):
        super().__init__(failures=False)
        self.fail_lookup = fail_lookup

    def rows(self, n, cycle, kind):
        rows = super().rows(n, cycle, kind)
        # real Schedule B rows carry their own committee_id; the agreed reader shape requires it
        return [{**row, 'committee_id': cc.cid(n)} for row in rows] if kind == 'B' else rows

    def data(self, endpoint, query):
        if endpoint.startswith('committee/') and endpoint.endswith('/totals/'):
            committee = endpoint.split('/')[1]
            return {'results': [{'committee_id': committee, 'committee_name': 'INERT COMMITTEE', 'cycle': query['cycle'], 'receipts': 1000.5, 'disbursements': 900.25,
                                 'recipient_committee_id': cc.cid(9), 'candidate_id': 'H0AA00001'}], 'pagination': {'count': 1, 'page': 1, 'pages': 1, 'per_page': 100}}
        if endpoint.startswith('candidate/'):
            if self.fail_lookup == 'page2':      # two pages, the second of which the provider then refuses
                return {'results': [{'committee_id': LOOKED_UP, 'designation': 'P'}], 'pagination': {'count': 2, 'page': 1, 'pages': 2, 'per_page': 100}}
            return {'results': [{'committee_id': LOOKED_UP, 'designation': 'P'}, {'committee_id': cc.cid(3), 'designation': 'A'}],
                    'pagination': {'count': 2, 'page': 1, 'pages': 1, 'per_page': 100}}
        return super().data(endpoint, query)

    def special(self, endpoint, query):
        if endpoint.startswith('candidate/') and self.fail_lookup == 'page2':
            return (b'{}', 503) if int(query.get('page', 1)) == 2 else None
        if endpoint.startswith('candidate/') and self.fail_lookup:
            return 'urlerror' if self.fail_lookup == 'urlerror' else (b'{}', 503)
        return None if endpoint.startswith(('committee/', 'candidate/')) else super().special(endpoint, query)


class Cross(cc.Base):
    def setUp(self):
        super().setUp()
        self.priority = self.root / 'priority.json'
        self.priority.write_text(json.dumps([{'candidate_id': 'H0AA00001', 'cycle': 2024}]))
        self.ccl_file = self.root / 'ccl24' / 'ccl.txt'
        self.ccl_file.parent.mkdir()
        self.ccl_file.write_text('H0AA00001|2024|2024|%s|H|P|1\n' % LOOKED_UP, encoding='utf-8')

    def live(self, opener=None, keys=cc.KEYS, cap=100000):
        return self.adapter(opener=opener or cc.ScriptedOpener(CampaignWorld(), delay=0.02), keys=keys, cap=cap, world=CampaignWorld())

    def flags(self, *extra, ccl=True, priority=True):
        flags = ['--seeds', str(self.seeds), '--live', '--include-later-cycle-context', '--init-new-ledger', '--enable-endpoint-contract', TOTALS_ID]
        if priority:
            flags += ['--priority-candidates', str(self.priority)] + (['--ccl', '2024=' + str(self.ccl_file)] if ccl else [])
        return flags + list(extra)

    def q(self, sql, *args, ledger=None):
        with closing(cc.read_only(ledger or self.state / 'frontier.sqlite')) as db:
            return db.execute(sql, args).fetchall()

    def run_main(self, extra, live=None, ccl=True, priority=True):
        self.write_seeds(cc.SEEDS)
        flags = self.flags(*extra, ccl=ccl, priority=priority)
        if (self.state / 'frontier.sqlite').exists():
            flags.remove('--init-new-ledger')
        printed = self.invoke(flags, live or self.live())
        receipt = Path(printed['receipt'])
        return receipt, json.loads(receipt.read_bytes())

    def test_n4_on_schema_3_with_totals_and_priority_is_sound_end_to_end(self):
        opener = cc.ScriptedOpener(CampaignWorld(), delay=0.03)
        receipt, data = self.run_main(['--workers', '4', '--max-requests', '5000'], live=self.live(opener=opener))
        self.assertEqual((data['workers'], data['ledger_schema_version'], data['stop']), (4, 3, None))
        self.assertGreaterEqual(data['peak_in_flight'], 2)
        self.assertEqual(self.q('PRAGMA user_version')[0][0], 3)
        self.assertEqual(data['http_attempts'], len(opener.calls))
        self.assertEqual((data['http_attempts'], data['endpoint_attempts_recorded'], data['attempts_recorded'], data['candidate_committee_lookup_http_attempts']), (len(opener.calls),) * 3 + (0,))
        self.assertEqual(self.q("SELECT COUNT(*) FROM endpoints WHERE kind IN('B','E_regular','E_notice','totals') AND status!='complete'")[0][0], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM endpoints WHERE kind='totals'")[0][0], self.q('SELECT COUNT(*) FROM units')[0][0], 'one totals endpoint per unit')
        # totals is never a payment: no edge, no payee reference, no leaf boundary, and its observations say so
        self.assertEqual(self.q("SELECT COUNT(*) FROM edges WHERE kind='totals'")[0][0] + self.q("SELECT COUNT(*) FROM candidate_references WHERE kind='totals'")[0][0]
                         + self.q("SELECT COUNT(*) FROM leaf_coverage WHERE kind='totals'")[0][0], 0)
        self.assertEqual(self.q("SELECT DISTINCT amount_status FROM observations WHERE kind='totals'"), [('not_a_payment_totals_row',)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM endpoint_retrievals")[0][0], self.q('SELECT COUNT(*) FROM pages')[0][0])
        self.assertEqual(self.q("SELECT COUNT(*) FROM candidate_committee_map")[0][0], 1)
        # the priority committee's B and totals endpoints were served in the first dispatch round
        priority_keys = {('schedules/schedule_b/', LOOKED_UP, '2024', None), ('committee/%s/totals/' % LOOKED_UP, None, '2024', None)}
        self.assertTrue(priority_keys <= {call[0] for call in opener.calls[:4]}, opener.calls[:4])
        # the discovery origin exists, is hint-tagged, and the derived closure reaches the unit it seeded
        frontier = driver.Frontier(self.state / 'frontier.sqlite', include_later_cycle_context=True, enabled_endpoint_contracts=[TOTALS_ID])
        self.addCleanup(frontier.close)
        ref, base = frontier.db.execute("SELECT origin_ref,base_metadata_json FROM origin_catalog WHERE origin_id='campaign-priority-H0AA00001-2024'").fetchone()
        self.assertIs(json.loads(base)['giving_or_payment_evidence'], False)
        self.assertTrue(frontier.closure().has((LOOKED_UP, 2024), ref))
        self.assertEqual(cc.conservation(self.state), {})

    def test_interrupted_n4_run_is_recovered_resumed_and_read_on_schema_3(self):
        commits = []
        original = driver.Frontier._commit

        def interrupted(frontier, job, outcome):
            commits.append(job.key)
            if len(commits) == 6:
                raise KeyboardInterrupt('inert operator interrupt')
            return original(frontier, job, outcome)
        with mock.patch.object(driver.Frontier, '_commit', interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_main(['--workers', '4', '--max-requests', '5000'], live=self.live(cc.ScriptedOpener(CampaignWorld(), delay=0.03)))
        self.assertEqual(len(list(self.state.glob('RECEIPT-*.json'))), 0)
        crashed, legacy = driver.unresolved_runs(self.state)
        self.assertEqual((len(crashed), legacy), (1, []))
        with self.assertRaises(SystemExit) as caught:
            self.invoke(['--seeds', str(self.seeds), '--max-requests', '5', '--live'], live=lambda *a, **k: self.fail('a refused start must not reach the transport'))
        self.assertIn('Unresolved collector run', str(caught.exception))
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), '--recover-crashed-runs']), redirect_stdout(out):
            driver.main()
        recovered = json.loads(out.getvalue())
        self.assertTrue(recovered['recovered'] and recovered['replayed_pairs'] >= 1, recovered)
        receipt = json.loads(Path(recovered['receipt']).read_bytes())
        self.assertTrue(receipt['recovered_after_crash'])
        self.assertEqual(receipt['stop']['error_type'], 'CrashRecovered')
        self.assertEqual(receipt['http_attempts'], self.q('SELECT COALESCE(SUM(attempt_count),0) FROM endpoints')[0][0])
        self.assertEqual(cc.conservation(self.state), {})
        with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), '--recover-crashed-runs']), redirect_stdout(io.StringIO()) as again:
            driver.main()
        self.assertFalse(json.loads(again.getvalue())['recovered'], 'recovery is idempotent')
        # resume to completion; the recovered-and-resumed ledger equals an uninterrupted N=1 run of the same world
        snapshot_one = self.snapshot(Path(recovered['receipt']))
        self.assertTrue(snapshot_one.exists())
        resumed, data = self.run_main(['--workers', '4', '--max-requests', '5000'])
        self.assertIsNone(data['stop'])
        self.assertEqual(cc.conservation(self.state), {})
        reference = self.root / 'reference'
        reference.mkdir()
        self.write_seeds(cc.SEEDS)
        ref_state = reference / 'state'
        self.invoke(self.flags('--workers', '1', '--max-requests', '5000'), self.live(cc.ScriptedOpener(CampaignWorld())), state=ref_state)
        mine, theirs = cc.canonical(self.state / 'frontier.sqlite'), cc.canonical(ref_state / 'frontier.sqlite')
        for table in ('units', 'origin_catalog', 'origin_seeds', 'observations', 'edges', 'pages', 'counterparty_conflicts', 'candidate_references', 'leaf_coverage'):
            self.assertEqual(mine[table], theirs[table], table)
        # the typed reader on the recovered-and-resumed ledger: the agreed shape, complete coverage for the priority committee
        accepted = self.state / 'accepted-batches' / 'batch-02'
        accepted.mkdir(parents=True)
        shutil.copyfile(self.state / 'frontier.sqlite', accepted / 'frontier.sqlite')
        (accepted / 'SNAPSHOT.json').write_text(json.dumps({'schema_version': 1, 'snapshot_sha256': hashlib.sha256((accepted / 'frontier.sqlite').read_bytes()).hexdigest()}))
        import committee_activity_reader as reader
        result = reader.read_campaign_spending(cycle=2024, candidate_id='H0AA00001', state_dir=self.state)
        self.assertEqual(list(result), ['query', 'snapshot', 'mapping', 'committees'])
        self.assertEqual(result['mapping']['committee_ids'], [LOOKED_UP])
        entry = result['committees'][0]
        self.assertEqual((entry['coverage_status'], entry['endpoint_status'], entry['row_occurrences']), ('complete', {'B': 'complete', 'totals': 'complete'}, 12))
        self.assertEqual(len(entry['totals']), 1)
        self.assert_shape(result)

    def assert_shape(self, result):
        schema_path = Path(__file__).resolve().parent / 'fixtures' / 'candidate_spending' / 'crawler' / 'read_campaign_spending_fixture.schema.json'
        self.assertEqual(hashlib.sha256(schema_path.read_bytes()).hexdigest(), 'def754dc60bd42a0323cf0ab56699db5fe76e52345854ef958caf6a8e969cc05')
        try:
            import jsonschema
        except ImportError:
            return
        schema = json.loads(schema_path.read_text(encoding='utf-8'))
        whole = {'$schema': 'https://json-schema.org/draft/2020-12/schema', '$ref': '#/$defs/Result', '$defs': schema['$defs']}   # the reader's result, not the fixture document
        errors = [error.message for error in jsonschema.Draft202012Validator(whole, format_checker=jsonschema.FormatChecker()).iter_errors(result)]
        self.assertEqual(errors, [])

    def lookup_conservation(self):
        """R1 per physical attempt, lookups included: every archived body is referenced exactly once (page, failure row or lookup attempt row) and
        SUM(endpoints.attempt_count) + SUM(lookups.http_attempts) equals the physical attempts of every receipt."""
        used = {}

        def hold(path):
            key = os.path.normcase(str(Path(path).resolve()))
            used[key] = used.get(key, 0) + 1
        with closing(cc.read_only(self.state / 'frontier.sqlite')) as db:
            for (path,) in db.execute('SELECT archive_path FROM pages'):
                hold(path)
            for (text,) in db.execute('SELECT archive_record_json FROM failures WHERE archive_record_json IS NOT NULL'):
                for path in re.findall(r'[^"]+\.body\.bin', json.dumps(json.loads(text))):
                    hold(path)
            for (text,) in db.execute('SELECT archive_refs_json FROM candidate_committee_lookups'):
                for ref in json.loads(text):
                    if ref.get('path'):
                        hold(ref['path'])
            endpoint_attempts = db.execute('SELECT COALESCE(SUM(attempt_count),0) FROM endpoints').fetchone()[0]
            lookup_attempts = db.execute('SELECT COALESCE(SUM(http_attempts),0) FROM candidate_committee_lookups').fetchone()[0]
        bodies = [os.path.normcase(str(path.resolve())) for folder in sorted(self.state.glob('responses-*')) for path in sorted(folder.glob('*.body.bin'))]
        problems = {}
        unreferenced = [body for body in bodies if used.get(body, 0) != 1]
        if unreferenced:
            problems['bodies_not_referenced_exactly_once'] = unreferenced
        receipts = sum(json.loads(path.read_bytes())['http_attempts'] for path in self.state.glob('RECEIPT-*.json'))
        if endpoint_attempts + lookup_attempts != receipts:
            problems['attempt_sum'] = (endpoint_attempts, lookup_attempts, receipts)
        return problems

    def test_a_failed_lookup_retried_in_the_next_run_keeps_every_attempt_row_and_every_archive_pair(self):
        failing = cc.ScriptedOpener(CampaignWorld(fail_lookup=True))
        first, one = self.run_main(['--workers', '2', '--max-requests', '5', '--enable-endpoint-contract', LOOKUP_ID], live=self.live(opener=failing), ccl=False)
        self.assertEqual((one['candidate_committee_lookup_http_attempts'], one['candidate_committee_api_lookup']['failed'], one['stop']['error_type']), (1, 1, 'FECAdmissionRefused'))
        self.assertEqual(self.lookup_conservation(), {})
        self.assertEqual(self.q('SELECT COUNT(*) FROM candidate_committee_map')[0][0], 0)
        working = cc.ScriptedOpener(CampaignWorld())
        second, two = self.run_main(['--workers', '2', '--max-requests', '5', '--enable-endpoint-contract', LOOKUP_ID], live=self.live(opener=working), ccl=False)
        self.assertEqual((two['candidate_committee_lookup_http_attempts'], two['candidate_committee_api_lookup']['found']), (1, 1))
        rows = self.q("SELECT attempt,status,run_id,http_attempts,archive_refs_json FROM candidate_committee_lookups WHERE source='api_candidate_committees' ORDER BY attempt")
        self.assertEqual([(row[0], row[1], row[3]) for row in rows], [(1, 'failed', 1), (2, 'found', 1)], 'both attempts are rows; the failed one was not replaced')
        starts = sorted(json.loads(path.read_text())['run_id'] for path in self.state.glob('RUN-*.START.json'))
        self.assertEqual(sorted(row[2] for row in rows), starts, 'each row names its own run')
        first_refs = json.loads(rows[0][4])
        self.assertEqual([ref['status'] for ref in first_refs], [503])
        self.assertTrue(Path(first_refs[0]['path']).is_file() and Path(first_refs[0]['receipt_path']).is_file(), 'the failed attempt\'s pair still exists and is still referenced')
        self.assertEqual(self.lookup_conservation(), {}, 'the attempt sums hold and no pair is orphaned')
        self.assertEqual(self.q('SELECT attempt,status FROM candidate_committee_lookups_current WHERE source=?', 'api_candidate_committees'), [(2, 'found')])
        self.assertEqual(self.q('SELECT committee_id,source FROM candidate_committee_map'), [(LOOKED_UP, 'api_candidate_committees')])
        self.assertEqual(len(failing.calls) + len(working.calls), one['http_attempts'] + two['http_attempts'])
        # a third run does not look again: a successful lookup ends it
        third, three = self.run_main(['--workers', '2', '--max-requests', '3', '--enable-endpoint-contract', LOOKUP_ID], live=self.live(), ccl=False)
        self.assertEqual((three['candidate_committee_lookup_http_attempts'], three['candidate_committee_api_lookup']['lookups']), (0, 0))
        self.assertEqual(self.q("SELECT COUNT(*) FROM candidate_committee_lookups WHERE source='api_candidate_committees'")[0][0], 2)

    def test_a_multi_page_lookup_that_fails_on_page_2_keeps_the_pairs_of_both_pages(self):
        opener = cc.ScriptedOpener(CampaignWorld(fail_lookup='page2'))
        receipt, data = self.run_main(['--workers', '2', '--max-requests', '5', '--enable-endpoint-contract', LOOKUP_ID], live=self.live(opener=opener), ccl=False)
        row = self.q("SELECT status,http_attempts,archive_refs_json FROM candidate_committee_lookups WHERE source='api_candidate_committees'")[0]
        self.assertEqual((row[0], row[1], data['candidate_committee_lookup_http_attempts']), ('failed', 2, 2))
        refs = json.loads(row[2])
        self.assertEqual([ref['status'] for ref in refs], [200, 503], 'page 1 and the refused page 2 are both referenced')
        self.assertTrue(all(Path(ref['path']).is_file() for ref in refs))
        self.assertEqual(self.lookup_conservation(), {})

    def test_local_mapping_reads_are_recorded_once_and_the_source_never_replaces_or_deletes_an_attempt_row(self):
        source = (Path(driver.__file__)).read_text(encoding='utf-8')
        self.assertNotIn('OR REPLACE INTO candidate_committee_lookups', source)
        self.assertNotIn('DELETE FROM candidate_committee_lookups', source)
        frontier = driver.Frontier(self.root / 'local.sqlite')
        self.addCleanup(frontier.close)
        spec = {2024: {'path': self.ccl_file, 'vintage': 'v1'}}
        for _ in range(3):
            frontier.set_priority([('H0AA00009', 2024)], ccl=spec)
        self.assertEqual([tuple(row) for row in frontier.db.execute("SELECT attempt,status,http_attempts FROM candidate_committee_lookups WHERE source='local_ccl'")], [(1, 'none_found', 0)])
        frontier.set_priority([('H0AA00009', 2024)], ccl={2024: {'path': self.ccl_file, 'vintage': 'v2'}})
        self.assertEqual([row[0] for row in frontier.db.execute("SELECT attempt FROM candidate_committee_lookups WHERE source='local_ccl' ORDER BY attempt")], [1, 2], 'a different read is a new attempt')

    def interrupted(self, where, extra=(), world=None):
        """Run main() and interrupt the writer at `where` ('first-dispatch' = after the lookups, before any endpoint page; 'first-commit' = in flight)."""
        count = []

        def hook(frontier, *args):
            count.append(1)
            raise KeyboardInterrupt('inert operator interrupt')
        name = '_next_rows' if where == 'first-dispatch' else '_commit'
        opener = cc.ScriptedOpener(world or CampaignWorld(), delay=0.02)
        with mock.patch.object(driver.Frontier, name, hook):
            with self.assertRaises(KeyboardInterrupt):
                self.run_main(['--workers', '2', '--max-requests', '40', '--enable-endpoint-contract', LOOKUP_ID] + list(extra), live=self.live(opener=opener), ccl=False)
        return opener

    def recover(self):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), '--recover-crashed-runs']), redirect_stdout(out):
            driver.main()
        return json.loads(out.getvalue())

    def test_a_crash_after_the_candidate_lookups_is_recovered_with_their_attempts_in_http_attempts(self):
        for where in ('first-dispatch', 'first-commit'):
            with self.subTest(crash=where):
                self.state = self.root / ('state-' + where)
                opener = self.interrupted(where)
                self.assertEqual(len(list(self.state.glob('RECEIPT-*.json'))), 0)
                self.assertEqual(self.q('SELECT COALESCE(SUM(http_attempts),0) FROM candidate_committee_lookups')[0][0], 1, 'the lookup committed before the crash')
                recovered = self.recover()
                self.assertTrue(recovered['recovered'] and recovered['receipt'], 'lookups ran, so the run is not abandoned')
                receipt = json.loads(Path(recovered['receipt']).read_bytes())
                self.assertEqual(receipt['candidate_committee_lookup_http_attempts'], 1)
                self.assertEqual(receipt['http_attempts'], receipt['endpoint_attempts_recorded'] + 1)
                self.assertEqual(receipt['http_attempts'], len(opener.calls), 'every physical attempt is counted: lookups and replayed in-flight pages')
                self.assertEqual(receipt['endpoint_attempts_recorded'], self.q('SELECT COALESCE(SUM(attempt_count),0) FROM endpoints')[0][0])
                self.assertTrue(receipt['recovered_after_crash'])
                self.assertEqual(self.lookup_conservation(), {})
        # lookups of an EARLIER run are not this run's: run 1 completes with a lookup, run 2 crashes without one
        self.state = self.root / 'state-earlier'
        self.run_main(['--workers', '2', '--max-requests', '6', '--enable-endpoint-contract', LOOKUP_ID], live=self.live(), ccl=False)
        self.assertEqual(self.q('SELECT COALESCE(SUM(http_attempts),0) FROM candidate_committee_lookups')[0][0], 1)
        self.interrupted('first-commit')
        receipt = json.loads(Path(self.recover()['receipt']).read_bytes())
        self.assertEqual((receipt['candidate_committee_lookup_http_attempts'], receipt['http_attempts']), (0, receipt['endpoint_attempts_recorded']))
        self.assertEqual(self.lookup_conservation(), {})

    def test_a_lookup_that_never_got_a_response_still_counts_and_stops_the_run_being_abandoned(self):
        opener = self.interrupted('first-dispatch', world=CampaignWorld(fail_lookup='urlerror'))
        self.assertEqual(len(opener.calls), 1, 'one physical attempt, no response, no archive pair')
        self.assertEqual(list(self.state.glob('responses-*/*')), [])
        recovered = self.recover()
        self.assertIsNotNone(recovered['receipt'], 'an attempt with no pair is still an attempt: not abandoned')
        self.assertEqual(list(self.state.glob('RUN-*.ABANDONED.json')), [])
        receipt = json.loads(Path(recovered['receipt']).read_bytes())
        self.assertEqual((receipt['http_attempts'], receipt['endpoint_attempts_recorded'], receipt['candidate_committee_lookup_http_attempts']), (1, 0, 1))
        self.assertEqual(self.lookup_conservation(), {})

    def test_a_crash_after_lookups_with_nothing_else_leaves_a_receipt_not_an_abandoned_marker(self):
        self.interrupted('first-dispatch')
        recovered = self.recover()
        self.assertIsNotNone(recovered['receipt'])
        self.assertEqual(list(self.state.glob('RUN-*.ABANDONED.json')), [])
        receipt = json.loads(Path(recovered['receipt']).read_bytes())
        self.assertEqual((receipt['http_attempts'], receipt['endpoint_attempts_recorded'], receipt['candidate_committee_lookup_http_attempts']), (1, 0, 1))


    def test_candidate_committee_lookups_are_accounted_separately_and_the_receipt_guard_holds(self):
        opener = cc.ScriptedOpener(CampaignWorld())
        receipt, data = self.run_main(['--workers', '2', '--max-requests', '40', '--enable-endpoint-contract', LOOKUP_ID], live=self.live(opener=opener), ccl=False)
        lookup = data['candidate_committee_api_lookup']
        self.assertEqual((lookup['lookups'], lookup['found'], lookup['http_attempts'], data['candidate_committee_lookup_http_attempts']), (1, 1, 1, 1))
        self.assertEqual(data['http_attempts'], data['endpoint_attempts_recorded'] + 1)
        self.assertEqual(data['http_attempts'], len(opener.calls))
        self.assertEqual(self.q('SELECT COALESCE(SUM(attempt_count),0) FROM endpoints')[0][0], data['endpoint_attempts_recorded'])
        mapped = self.q('SELECT committee_id,source FROM candidate_committee_map')
        self.assertEqual(mapped, [(LOOKED_UP, 'api_candidate_committees')], 'only the designation-P committee is kept')
        detail = json.loads(self.q("SELECT detail_json FROM candidate_committee_lookups WHERE source='api_candidate_committees'")[0][0])
        self.assertTrue(all(Path(page['path']).is_file() for page in detail['archive_pages']))
        self.assertEqual(self.q("SELECT COUNT(*) FROM units WHERE committee_id=?", LOOKED_UP)[0][0] > 0, True)
        # the guard: a lookup page the receipt does not account for is refused, with no receipt written
        self.state = self.root / 'state2'      # a second, empty state directory
        with mock.patch.object(driver.Frontier, 'fill_priority_via_api', lambda frontier, transport: {**original_lookup(frontier, transport), 'http_attempts': 0}):
            with self.assertRaises(RuntimeError) as caught:
                self.run_main(['--workers', '2', '--max-requests', '40', '--enable-endpoint-contract', LOOKUP_ID], live=self.live(), ccl=False)
        self.assertIn('did not record every physical attempt', str(caught.exception))
        self.assertEqual(list(self.state.glob('RECEIPT-*.json')), [])

    def transport(self, folder, opener=None, maximum=1000):
        transport = accounted.AccountedTransport(list(cc.KEYS[:3]), self.root / 'quota.sqlite', folder, maximum, opener=opener or cc.ScriptedOpener(CampaignWorld()), cap=1000)
        self.addCleanup(transport.close)
        return transport

    def test_an_uncommitted_totals_page_is_replayed_by_recovery_even_though_its_endpoint_is_path_templated(self):
        frontier = driver.Frontier(self.root / 'recover.sqlite', enabled_endpoint_contracts=[TOTALS_ID])
        self.addCleanup(frontier.close)
        frontier.seed([{'committee_id': cc.cid(1), 'cycle': 2024, 'origins': ['industryA']}])
        transport = self.transport(self.root / ('responses-' + '0' * 32))
        row = frontier.db.execute("SELECT * FROM endpoints WHERE kind='totals'").fetchone()
        job = frontier._prepare(row)
        self.assertEqual(job.endpoint, 'committee/%s/totals/' % cc.cid(1))
        outcome = driver._fetch(transport, job, threading.Event(), True)
        self.assertIsNone(outcome.error)            # archived, not committed: the process "died" here
        work = frontier.recover(transport.archive, self.root / 'quarantine')
        self.assertEqual((work['replayed_pairs'], work['quarantined']), (1, []))
        status = frontier.db.execute("SELECT status,pages_stored,attempt_count FROM endpoints WHERE kind='totals'").fetchone()
        self.assertEqual(tuple(status), ('complete', 1, 1))
        self.assertEqual(frontier.db.execute('SELECT COUNT(*) FROM endpoint_retrievals').fetchone()[0], 1)

    def test_lookup_pages_are_referenced_when_committed_and_quarantined_never_replayed_when_not(self):
        frontier = driver.Frontier(self.root / 'lookup.sqlite', enabled_endpoint_contracts=[LOOKUP_ID])
        self.addCleanup(frontier.close)
        frontier.set_priority([('H0AA00001', 2024)])
        folder = self.root / ('responses-' + '1' * 32)
        outcome = frontier.fill_priority_via_api(self.transport(folder))
        self.assertEqual((outcome['found'], outcome['http_attempts']), (1, 1))
        work = frontier.recover(folder, self.root / 'q1')
        self.assertEqual((work['referenced_pairs'], work['replayed_pairs'], work['quarantined']), (1, 0, []))
        with frontier.transaction():      # the process died before the lookup's rows committed
            frontier.db.execute('DELETE FROM candidate_committee_lookups')      # (test setup only: simulates a crash before the lookup's rows committed)
            frontier.db.execute('DELETE FROM candidate_committee_map')
        work = frontier.recover(folder, self.root / 'q2')
        self.assertEqual((work['referenced_pairs'], work['replayed_pairs'], len(work['quarantined'])), (0, 0, 2))
        self.assertTrue(all('lookup page' in item['reason'] for item in work['quarantined']))
        self.assertEqual([p.name for p in folder.iterdir()], [])

    @unittest.skipUnless(REAL_SNAPSHOT.is_file() and list(REAL_STATE.glob('seeds-*.json')), 'REAL: accepted batch-02 snapshot and its captured seeds are not present')
    def test_REAL_migrated_batch02_copy_is_opened_by_this_collector_with_a_zero_attempt_run(self):
        seeds = sorted(REAL_STATE.glob('seeds-*.json'))[0]
        before = hashlib.sha256(REAL_SNAPSHOT.read_bytes()).hexdigest()
        self.state.mkdir()
        proof = driver.migrate_to_schema3(REAL_SNAPSHOT, self.state / 'frontier.sqlite', later_cycle_context=True, seed_file=seeds, snapshot_proof=REAL_SNAPSHOT.with_name('SNAPSHOT.json'))
        self.assertEqual((proof['result'], proof['counts']['closure_rows_derived']), ('lossless', 134317))
        self.assertEqual(hashlib.sha256(REAL_SNAPSHOT.read_bytes()).hexdigest(), before, 'the accepted snapshot was only read')
        migrated = hashlib.sha256((self.state / 'frontier.sqlite').read_bytes()).hexdigest()
        failures_before = self.q('SELECT COUNT(*) FROM failures')[0][0]      # batch 02 itself recorded four
        out = io.StringIO()
        created = []

        def live(archive, maximum, **options):
            transport = accounted.AccountedTransport(list(cc.KEYS[:2]), self.root / 'quota.sqlite', archive, maximum, opener=cc.ScriptedOpener(CampaignWorld()), cap=1000)
            created.append(transport)
            return transport
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=live), redirect_stdout(out):
            with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), '--seeds', str(seeds), '--max-requests', '0', '--live', '--include-later-cycle-context']):
                driver.main()
        data = json.loads(Path(json.loads(out.getvalue().strip().splitlines()[-1])['receipt']).read_bytes())
        self.assertEqual((data['http_attempts'], data['endpoint_attempts_recorded'], data['new_pages_this_invocation'], created[0].http_attempts), (0, 0, 0, 0))
        self.assertEqual(data['stop']['error_type'], 'FECAdmissionRefused')
        self.assertEqual((data['ledger_schema_version'], data['units'], data['origin_catalog_entries'], data['origin_seed_assignments'], data['origin_unit_assignments']), (3, 13141, 46521, 46521, 134317))
        self.assertTrue(data['later_cycle_context_explicitly_enabled'] and data['lower_bound'] and data['closure_basis'] == 'collected_edges_only')
        self.assertEqual(self.q('PRAGMA quick_check')[0][0], 'ok')
        self.assertNotEqual(hashlib.sha256((self.state / 'frontier.sqlite').read_bytes()).hexdigest(), migrated, 'the run recorded its own failure row')
        self.assertEqual(self.q('SELECT COUNT(*) FROM failures')[0][0], failures_before + 1)
        self.assertEqual(hashlib.sha256(REAL_SNAPSHOT.read_bytes()).hexdigest(), before)


original_lookup = driver.Frontier.fill_priority_via_api

if __name__ == '__main__':
    unittest.main()
