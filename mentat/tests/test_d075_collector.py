"""Inert D075 controls for the collector side of the container runtime: stored path spellings, main() in `--transport broker` mode (receipts, START and ledger paths in the
Windows spelling, family and descriptor checks before anything is written), --run-id, --stop-after-seconds, the broker-failure classes, and the fcntl run lease.
The default (local) runtime is pinned unchanged: its receipt and START records carry exactly the R1b field sets. No credentials, provider or network.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import d075_support as s
import test_collect_committee_activity_concurrency as cc
import test_r1b_cross as cross
import stored_paths as sp

driver, accounted = s.driver, s.accounted
TOTALS_ID = cross.TOTALS_ID
REAL_STATE = cross.REAL_STATE
# the R1b receipt of a plain N=1 run (55 fields) and its START record (16 fields), as dumped by R1b-RECEIPT-FIELDS.json: the local runtime must not change either
R1B_RECEIPT = set('''UI_top_depth_or_page_limits_used achieved_requests_per_hour attempts_recorded candidate_beneficiary_or_unverified_references candidate_resolutions_required closure_basis
counterparty_identity_conflicts edges endpoint_attempts_recorded endpoint_contract_enablement endpoint_status_counts exhaustive_disclosed_spending_complete failed_attempt_allowance generated_utc
halted_unadmitted_jobs http_attempts incoming_receipts_not_requested_means_unknown_not_empty later_context_missing_when_disabled later_cycle_context_explicitly_enabled leaf_disclosure_boundaries
ledger ledger_schema_version ledger_sha256 legacy_snapshot_compatible lower_bound new_pages_this_invocation no_B_E_grand_total no_exact_origin_dollar_attribution observations
candidate_committee_lookup_http_attempts origin_catalog_entries origin_seed_assignments origin_unit_assignments origin_unit_assignments_note other_spending_families pages peak_in_flight
physical_request_cap campaign_priority rate_limited_endpoints_skipped rate_limited_responses raw_archive_directory required_unsupported_endpoints rolling_10min_requests_per_hour_last
rolling_60s_requests_per_hour_peak run_seconds schema_version seed_receipt snapshots_json_or_evidence_json_published stop supported_outgoing_endpoint_kinds
supported_outgoing_endpoints_complete units unknown_or_subcent_amount_occurrences workers'''.split())
R1B_START = set('''adapter_sha256 collector_sha256 enabled_endpoint_contracts init_new_ledger later_cycle_context ledger_at_start max_failed_attempts max_requests optional_receipts
priority_candidates_file purpose run_id schema_version seed_receipt started_utc workers'''.split())


class StoredPaths(unittest.TestCase):
    MAP = '/hoststate=%s;/frontier=%s;/export=%s\\exports' % (s.FAKE_STORED, s.FAKE_STORED, s.FAKE_STORED)

    def test_unset_or_empty_is_the_identity_in_both_directions(self):
        for text in ('', None, '  ;  '):
            self.assertEqual(sp.parse(text), [])
        for value in ('/frontier/frontier.sqlite', s.FAKE_STORED + '\\x', 'C:\\anything\\else', Path('rel/path')):
            self.assertEqual(sp.to_stored(value, []), str(value))
            self.assertEqual(sp.to_host(value, []), str(value))
        with mock.patch.dict(os.environ, {sp.VARIABLE: ''}):
            self.assertEqual(sp.pairs(), [])

    def test_a_container_path_is_recorded_in_the_windows_spelling_and_read_back_through_the_first_mount_listed(self):
        pairs = sp.parse(self.MAP)
        self.assertEqual(sp.to_stored('/frontier/frontier.sqlite', pairs), s.FAKE_STORED + '\\frontier.sqlite')
        self.assertEqual(sp.to_stored('/hoststate/responses-abc/attempt-000001-aa.body.bin', pairs), s.FAKE_STORED + '\\responses-abc\\attempt-000001-aa.body.bin')
        self.assertEqual(sp.to_stored(Path('/export/run1/EXPORT.json'), pairs), s.FAKE_STORED + '\\exports\\run1\\EXPORT.json')
        self.assertEqual(sp.to_stored('/frontier', pairs), s.FAKE_STORED)
        self.assertEqual(sp.to_host(s.FAKE_STORED + '\\responses-abc\\a.json', pairs), '/hoststate/responses-abc/a.json', 'a tie goes to the first pair listed: the read-only host view')
        self.assertEqual(sp.to_host(s.FAKE_STORED + '\\exports\\run1\\EXPORT.json', pairs), '/export/run1/EXPORT.json', 'the longest stored root wins')
        self.assertEqual(sp.to_host(s.FAKE_STORED.lower() + '\\RESPONSES-ABC\\A.JSON', pairs), '/hoststate/RESPONSES-ABC/A.JSON', 'Windows spellings compare without case')
        self.assertEqual(sp.to_host(s.FAKE_STORED.replace('\\', '/') + '/x.json', pairs), '/hoststate/x.json', 'and without separator style')
        self.assertEqual(sp.to_host(s.FAKE_STORED, pairs), '/hoststate')

    def test_only_whole_path_components_match_and_everything_else_passes_through(self):
        pairs = sp.parse(self.MAP)
        self.assertEqual(sp.to_stored('/frontier2/x', pairs), '/frontier2/x')
        self.assertEqual(sp.to_stored('/frontierX', pairs), '/frontierX')
        self.assertEqual(sp.to_stored('/mentat/mentat/tools', pairs), '/mentat/mentat/tools')
        self.assertEqual(sp.to_host(s.FAKE_STORED + 'X\\x', pairs), s.FAKE_STORED + 'X\\x')
        self.assertEqual(sp.to_host('C:\\Users\\other\\file', pairs), 'C:\\Users\\other\\file')
        self.assertEqual(sp.to_host('/hoststate/x', pairs), '/hoststate/x', 'a container path is already readable')
        self.assertTrue(sp.same(s.FAKE_STORED + '\\', s.FAKE_STORED.lower().replace('\\', '/')))
        self.assertFalse(sp.same(s.FAKE_STORED + 'x', s.FAKE_STORED))

    def test_a_malformed_map_is_refused(self):
        for text in ('/a', '=b', '/a=', '/a=C:\\x;/a=C:\\y', '/a/=C:\\x'):
            with self.subTest(text):
                with self.assertRaises(ValueError):
                    sp.parse(text)

    def test_a_round_trip_through_a_windows_container_prefix_keeps_the_prefix_spelling(self):
        pairs = sp.parse('C:\\Temp\\x\\host=' + s.FAKE_STORED)
        stored = sp.to_stored('C:\\Temp\\x\\host\\responses-1\\a.bin', pairs)
        self.assertEqual(stored, s.FAKE_STORED + '\\responses-1\\a.bin')
        self.assertEqual(sp.to_host(stored, pairs), 'C:\\Temp\\x\\host\\responses-1\\a.bin')

    @unittest.skipUnless(list(REAL_STATE.glob('RECEIPT-*.json')), 'the accepted batches 01-03 receipts are not present')
    def test_the_stored_root_is_byte_equal_to_what_batches_01_to_03_recorded(self):
        pairs = sp.parse(self.MAP)
        receipts = sorted(REAL_STATE.glob('RECEIPT-*.json'))
        self.assertGreaterEqual(len(receipts), 2)
        for receipt in receipts:
            data = json.loads(receipt.read_bytes())
            run = receipt.name[len('RECEIPT-'):-len('.json')]
            self.assertEqual(data['ledger'], sp.to_stored('/frontier/frontier.sqlite', pairs), receipt.name)
            self.assertEqual(data['raw_archive_directory'], sp.to_stored('/hoststate/responses-' + run, pairs), receipt.name)
            self.assertEqual(data['seed_receipt']['captured_copy'], sp.to_stored('/frontier/seeds-' + data['seed_receipt']['sha256'] + '.json', pairs), receipt.name)


class Base(cc.Base):
    def setUp(self):
        super().setUp()
        self.brokers = []
        self.addCleanup(lambda: [b.shutdown() for b in self.brokers])

    def broker(self, **options):
        broker = s.make_broker(self.root, **options)
        self.brokers.append(broker)
        return broker

    def run_main(self, broker, flags=(), *, mapping=None, later=True, rows=cc.SMALL_SEEDS, init=True, **kw):
        self.write_seeds(rows)
        environment = {sp.VARIABLE: mapping} if mapping else {}
        argv = [*(['--include-later-cycle-context'] if later else []), *(['--init-new-ledger'] if init else []), '--max-requests', '100000', *flags]
        with mock.patch.dict(os.environ, environment):
            return s.invoke_broker(self, broker, argv, state=self.state, run_id=broker.run_id, **kw)

    def mapping(self, broker):
        return '%s=%s;%s=%s' % (broker.archive_root, s.FAKE_STORED, self.state, s.FAKE_STORED)


class BrokerMain(Base):
    def test_a_broker_run_records_the_windows_spelling_everywhere_and_reads_it_back_through_the_mounts(self):
        broker = self.broker(stored_root=s.FAKE_STORED)
        printed = self.run_main(broker, mapping=self.mapping(broker))
        receipt = json.loads(Path(printed['receipt']).read_bytes())
        start = json.loads((self.state / ('RUN-' + s.RUN + '.START.json')).read_bytes())
        self.assertEqual(receipt['ledger'], s.FAKE_STORED + '\\frontier.sqlite')
        self.assertEqual(receipt['raw_archive_directory'], s.FAKE_STORED + '\\responses-' + s.RUN)
        digest = receipt['seed_receipt']['sha256']
        self.assertEqual(receipt['seed_receipt']['captured_copy'], s.FAKE_STORED + '\\seeds-' + digest + '.json')
        self.assertEqual(start['seed_receipt']['captured_copy'], receipt['seed_receipt']['captured_copy'])
        self.assertTrue((self.state / ('seeds-' + digest + '.json')).is_file(), 'the copy itself sits in the real state folder; only its recorded name is the stored one')
        self.assertEqual(receipt['transport']['kind'], 'broker')
        self.assertEqual((receipt['transport']['keys_held_by_this_process'], receipt['transport']['stored_root'], receipt['transport']['archive_directory']),
                         (0, s.FAKE_STORED, s.FAKE_STORED + '\\responses-' + s.RUN))
        self.assertEqual(start['transport']['descriptor_sha256'], receipt['transport']['descriptor_sha256'])
        self.assertIsNone(start['transport']['archive_directory'], 'the START record is written before the broker opens the folder')
        with closing(sqlite3.connect(self.state / 'frontier.sqlite')) as db:
            paths = [row[0] for row in db.execute('SELECT archive_path FROM pages')]
            failures = [json.loads(row[0]) for row in db.execute('SELECT archive_record_json FROM failures WHERE archive_record_json IS NOT NULL')]
            digests = dict(db.execute('SELECT archive_path, archive_sha256 FROM pages'))
        self.assertTrue(paths and all(p.startswith(s.FAKE_STORED + '\\responses-' + s.RUN + '\\attempt-') for p in paths))
        for path in paths:      # the validator's way of reading a page: the recorded spelling, mapped to a file that hashes as the ledger says
            real = Path(sp.to_host(path, sp.parse(self.mapping(broker))))
            self.assertEqual(hashlib.sha256(real.read_bytes()).hexdigest(), digests[path])
        for record in failures:
            self.assertTrue(record['path'].startswith(s.FAKE_STORED + '\\responses-'))
            self.assertTrue(record['receipt_path'].startswith(s.FAKE_STORED + '\\responses-'))
        bodies = len(list(s.folder_of(broker).glob('*.body.bin')))
        self.assertEqual(bodies, len(paths) + len([record for record in failures if record.get('raw_body_persisted')]), 'every archived pair is a page or a failure body')
        self.assertEqual(receipt['http_attempts'], receipt['endpoint_attempts_recorded'] + receipt['candidate_committee_lookup_http_attempts'])
        broker.shutdown()
        self.assertEqual(receipt['http_attempts'], json.loads(broker.receipt_path.read_text())['attempts_journalled'])

    def test_the_contract_binding_path_is_recorded_in_the_stored_spelling_but_verified_where_it_lives(self):
        families = ['B', 'E', 'totals']
        broker = self.broker(stored_root=s.FAKE_STORED, families=families, opener=cc.ScriptedOpener(cross.CampaignWorld()))
        printed = self.run_main(broker, ['--enable-endpoint-contract', TOTALS_ID], mapping=self.mapping(broker))
        receipt = json.loads(Path(printed['receipt']).read_bytes())
        binding = receipt['endpoint_contract_enablement']['binding']
        self.assertEqual(binding['path'], s.FAKE_STORED + '\\frontier.endpoint-contracts.json')
        self.assertTrue((self.state / 'frontier.endpoint-contracts.json').is_file())
        self.assertEqual(receipt['transport']['families'], families)
        with closing(sqlite3.connect(self.state / 'frontier.sqlite')) as db:
            self.assertGreater(db.execute("SELECT COUNT(*) FROM pages WHERE kind='totals'").fetchone()[0], 0, 'totals pages came through the broker')

    def test_the_local_runtime_is_unchanged_its_records_carry_exactly_the_r1b_fields_and_no_transport(self):
        self.write_seeds(cc.SMALL_SEEDS)
        receipt, data = self.collect(['--init-new-ledger', '--max-requests', '100000'], self.adapter(world=cc.World(failures=False)))
        start = json.loads(next(self.state.glob('RUN-*.START.json')).read_bytes())
        self.assertEqual(set(data), R1B_RECEIPT)
        self.assertEqual(set(start), R1B_START)
        self.assertEqual(data['ledger'], str(self.state / 'frontier.sqlite'))
        self.assertEqual(data['raw_archive_directory'], str(self.state / ('responses-' + data['raw_archive_directory'].rsplit('-', 1)[1])))

    def test_a_missing_family_a_foreign_descriptor_and_a_foreign_spelling_stop_the_run_before_anything_is_written(self):
        broker = self.broker(opener=cc.ScriptedOpener(cross.CampaignWorld()))
        with self.assertRaises(SystemExit) as refused:
            self.run_main(broker, ['--enable-endpoint-contract', TOTALS_ID])
        self.assertIn('does not admit family totals', str(refused.exception))
        self.assertIn('Nothing was written', str(refused.exception))
        self.assertFalse(self.state.exists(), 'no state folder, ledger, lease, seed copy or START record')
        with self.assertRaises(SystemExit) as refused:
            self.run_main(broker, ['--optional-receipts'])
        self.assertIn('family A', str(refused.exception))
        with self.assertRaises(SystemExit) as refused:             # another run's descriptor
            s.invoke_broker(self, broker, ['--max-requests', '5', '--init-new-ledger'], state=self.state, run_id='cd' * 16)
        self.assertIn('another collector run', str(refused.exception))
        other = self.broker(stored_root='D:\\elsewhere', run_id='cd' * 16, name='other')
        with self.assertRaises(SystemExit) as refused:             # the map says the archive root is spelled differently from what the broker records
            self.run_main(other, mapping=self.mapping(other))
        self.assertIn('would not read back', str(refused.exception))
        self.assertFalse(self.state.exists())
        with self.assertRaises(SystemExit) as refused:             # a cap above the token's
            self.run_main(self.broker(max_attempts=10, run_id='ef' * 16, name='third'))
        self.assertIn('cap', str(refused.exception))
        self.assertFalse(self.state.exists())

    def test_recovery_under_the_stored_map_matches_the_pages_the_ledger_holds_by_their_recorded_spelling(self):
        broker = self.broker(stored_root=s.FAKE_STORED)
        mapping = self.mapping(broker)
        counter, real = [], driver.Frontier._commit

        def commit(frontier, job, outcome):
            counter.append(1)
            if len(counter) == 4:
                raise Kill()
            return real(frontier, job, outcome)
        with mock.patch.object(driver.Frontier, '_commit', commit):
            with self.assertRaises(Kill):
                self.run_main(broker, mapping=mapping)
        self.assertEqual(driver.unresolved_runs(self.state, broker.archive_root), ([s.RUN], []))
        with mock.patch.dict(os.environ, {sp.VARIABLE: mapping}):
            printed = driver.recover_crashed_runs(self.state, broker.archive_root)
        self.assertEqual((printed['recovered'], printed['replayed_pairs']), (True, 1), 'three committed pages are recognised as held, the one orphan is replayed')
        receipt = json.loads(Path(printed['receipt']).read_bytes())
        self.assertEqual(receipt['raw_archive_directory'], s.FAKE_STORED + '\\responses-' + s.RUN)
        self.assertEqual(receipt['http_attempts'], len(list(s.folder_of(broker).glob('*.body.bin'))))
        with closing(sqlite3.connect(self.state / 'frontier.sqlite')) as db:
            paths = [row[0] for row in db.execute('SELECT archive_path FROM pages')]
        # The test seam (stored_root) respells only what the broker ANSWERS; the replayed orphan's page comes from the metadata file on disk, which a production broker writes in the stored
        # spelling anyway (its real path IS the stored one). So here exactly the replayed page keeps the scratch spelling.
        self.assertEqual(len([path for path in paths if path.startswith(s.FAKE_STORED + '\\responses-')]), len(paths) - 1)
        self.assertEqual(len(paths), 4)

    def test_an_archive_read_through_the_mounts_fails_closed_and_without_a_map_behaves_as_before(self):
        folder = self.root / 'host' / ('responses-' + s.RUN)
        folder.mkdir(parents=True)
        body = b'{"results": []}'
        (folder / 'a.body.bin').write_bytes(body)
        recorded = s.FAKE_STORED + '\\responses-' + s.RUN + '\\a.body.bin'
        evidence = {'path': recorded, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        mapping = '%s=%s' % (self.root / 'host', s.FAKE_STORED)
        with mock.patch.dict(os.environ, {sp.VARIABLE: mapping}):
            self.assertEqual(str(driver._verify_archive(evidence, 'refused')), recorded, 'the recorded spelling is what is kept')
            for name, bad in {'wrong hash': dict(evidence, sha256='0' * 64), 'wrong size': dict(evidence, bytes=1)}.items():
                with self.subTest(name), self.assertRaises(driver.ArchiveBindingFailed):
                    driver._verify_archive(bad, 'refused')
            (folder / 'a.body.bin').unlink()
            with self.assertRaises(driver.ArchiveBindingFailed):
                driver._verify_archive(evidence, 'refused')                              # a stale or missing read is a global stop, not a generic OSError
        with mock.patch.dict(os.environ, {sp.VARIABLE: ''}):
            plain = self.root / 'plain.body.bin'
            with self.assertRaises(OSError) as caught:
                driver._verify_archive(dict(evidence, path=str(plain)), 'refused')       # host-native: the old behaviour, untouched
            self.assertNotIsInstance(caught.exception, driver.ArchiveBindingFailed)
            plain.write_bytes(body)
            self.assertEqual(driver._verify_archive(dict(evidence, path=str(plain)), 'refused'), plain)
            with self.assertRaises(driver.ArchiveBindingFailed):
                driver._verify_archive(dict(evidence, path=str(plain), sha256='0' * 64), 'refused')

    def test_a_run_id_is_named_by_the_caller_and_never_reused(self):
        broker = self.broker()
        printed = self.run_main(broker)
        self.assertEqual(Path(printed['receipt']).name, 'RECEIPT-' + s.RUN + '.json')
        self.assertTrue((self.state / ('RUN-' + s.RUN + '.START.json')).is_file())
        self.assertTrue(s.folder_of(broker).is_dir())
        again = self.broker(name='again')
        with self.assertRaises(SystemExit) as refused:
            self.run_main(again, init=False)
        self.assertIn('was used before', str(refused.exception))
        for bad in ('xyz', 'A' * 32, 'ab' * 15):
            with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
                with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), '--seeds', str(self.seeds), '--live', '--max-requests', '1', '--run-id', bad]):
                    driver.main()

    def test_a_local_run_can_be_named_too_and_the_default_is_a_fresh_uuid(self):
        self.write_seeds(cc.SMALL_SEEDS)
        named = 'ef' * 16
        printed = self.invoke(['--seeds', str(self.seeds), '--live', '--init-new-ledger', '--max-requests', '3', '--run-id', named], self.adapter(world=cc.World(failures=False)))
        self.assertEqual(Path(printed['receipt']).name, 'RECEIPT-' + named + '.json')
        printed = self.invoke(['--seeds', str(self.seeds), '--live', '--max-requests', '3'], self.adapter(world=cc.World(failures=False)))
        self.assertRegex(Path(printed['receipt']).name, r'^RECEIPT-[0-9a-f]{32}\.json$')

    def test_the_offline_subcommands_take_none_of_the_new_run_options(self):
        self.write_seeds(cc.SMALL_SEEDS)
        self.invoke(['--seeds', str(self.seeds), '--live', '--init-new-ledger', '--max-requests', '2'], self.adapter(world=cc.World(failures=False)))
        for extra in (['--transport', 'broker'], ['--run-id', 'ab' * 16], ['--stop-after-seconds', '5']):
            with self.subTest(extra), self.assertRaises(SystemExit), mock.patch('sys.stderr'):
                with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), '--recover-crashed-runs', *extra]):
                    driver.main()
        for extra in (['--rebind-endpoint-contracts', '--enable-endpoint-contract', TOTALS_ID, '--archive-root', str(self.root)],):
            with self.subTest(extra), self.assertRaises(SystemExit), mock.patch('sys.stderr'):
                with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), *extra]):
                    driver.main()
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):            # --transport broker needs the archive root and the run id
            with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), '--seeds', str(self.seeds), '--live', '--max-requests', '1', '--transport', 'broker']):
                driver.main()
        with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), '--recover-crashed-runs', '--archive-root', str(self.root / 'nowhere')]):
            with mock.patch('builtins.print') as printed:
                driver.main()                                                     # an offline recovery may name an archive root; with nothing unresolved it says so
        self.assertEqual(json.loads(printed.call_args[0][0]), {'recovered': False, 'reason': 'No unresolved collector run.'})


class Kill(BaseException):
    """A process that dies: nothing catches it."""


class DeadlineAndClasses(Base):
    def test_stop_after_seconds_stops_dispatch_drains_and_a_resume_reaches_the_uninterrupted_ledger(self):
        reference, _, _, ref_report = cc.run_world(self.root, workers=1, adapter=True, world=cc.World(failures=False), seeds=cc.SMALL_SEEDS, name='whole', ledger=self.root / 'whole-q.sqlite')
        clock = [0.0]
        real = driver.time.monotonic

        class Opener(cc.ScriptedOpener):
            def open(inner, request, timeout):
                clock[0] += 1.0                                      # each physical request "takes" a second of the controlled clock
                return super().open(request, timeout)
        with mock.patch.object(driver.time, 'monotonic', lambda: real() + clock[0]):
            frontier = driver.Frontier(self.root / 'part.sqlite', include_later_cycle_context=True)
            frontier.seed(cc.SMALL_SEEDS)
            transport = accounted.AccountedTransport(list(cc.KEYS), self.root / 'part-q.sqlite', self.root / 'archive-part', 100000, opener=Opener(cc.World(failures=False)))
            first = frontier.run(transport, stop_after_seconds=5.5)
            transport.close()
            frontier.close()
        self.assertEqual(first['stop']['error_type'], 'DeadlineReached')
        self.assertFalse(first['stop']['global_admission_archive_auth_quota_or_failure_allowance'])
        self.assertEqual(first['attempts_recorded'], transport.http_attempts)
        self.assertEqual(transport.http_attempts, 6, 'dispatch stopped at the first look after the allowance, with nothing in flight lost')
        self.assertFalse(first['supported_outgoing_endpoints_complete'])
        frontier = driver.Frontier(self.root / 'part.sqlite', include_later_cycle_context=True)
        transport = accounted.AccountedTransport(list(cc.KEYS), self.root / 'part-q.sqlite', self.root / 'archive-part2', 100000, opener=cc.ScriptedOpener(cc.World(failures=False)))
        rest = frontier.run(transport)
        transport.close()
        frontier.close()
        self.assertIsNone(rest['stop'])
        self.assertEqual(cc.canonical(self.root / 'part.sqlite'), cc.canonical(reference.path), 'interrupted at a deadline and resumed equals one uninterrupted run')

    def test_the_allowance_must_be_a_positive_number(self):
        frontier = driver.Frontier(self.root / 'f.sqlite', include_later_cycle_context=True)
        self.addCleanup(frontier.close)
        frontier.seed(cc.SMALL_SEEDS)
        transport = accounted.AccountedTransport(list(cc.KEYS), self.root / 'q.sqlite', self.root / 'a', 10, opener=cc.ScriptedOpener(cc.World()))
        self.addCleanup(transport.close)
        for bad in (0, -1, True, 'x', float('nan')):
            with self.subTest(bad), self.assertRaises(ValueError):
                frontier.run(transport, stop_after_seconds=bad)
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), '--seeds', str(self.seeds), '--live', '--max-requests', '1', '--stop-after-seconds', '0']):
                driver.main()

    def test_broker_unavailable_is_a_global_stop_that_leaves_the_endpoint_pending(self):
        error = s.pool.BrokerUnavailable('x')
        self.assertTrue(driver._is_global(error))
        self.assertEqual(driver._status(error), 'pending')
        self.assertFalse(driver._key_scoped(error))
        self.assertFalse(driver._is_global(ValueError('x')))


class Lease(unittest.TestCase):
    class FakeFcntl:
        LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

        def __init__(self, other_error=None):
            self.held, self.calls, self.other_error = False, [], other_error

        def flock(self, descriptor, operation):
            self.calls.append(operation)
            if self.other_error:
                raise self.other_error
            if operation & self.LOCK_UN:
                self.held = False
            elif self.held and operation & self.LOCK_NB:
                raise BlockingIOError(11, 'would block')
            else:
                self.held = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_the_posix_backend_takes_a_nonblocking_exclusive_flock_refuses_a_second_holder_and_releases(self):
        fake = self.FakeFcntl()
        with mock.patch.dict(sys.modules, {'fcntl': fake}):
            backend = driver.lease_backend('posix')
            self.assertIs(backend, driver._FcntlLease)
            with driver.run_lease(self.root, backend=backend):
                self.assertTrue(fake.held)
                with self.assertRaises(SystemExit) as caught:
                    with driver.run_lease(self.root, backend=backend):
                        self.fail('a second holder must not run')
                self.assertIn('run lease', str(caught.exception))
                self.assertTrue(fake.held, 'the first holder keeps its lock when a second is refused')
            self.assertFalse(fake.held)
            with driver.run_lease(self.root, backend=backend):
                pass
        self.assertEqual(fake.calls[0], fake.LOCK_EX | fake.LOCK_NB)
        self.assertIn(fake.LOCK_UN, fake.calls)

    def test_any_other_lock_error_is_not_swallowed(self):
        fake = self.FakeFcntl(other_error=OSError(37, 'no locks available'))
        with mock.patch.dict(sys.modules, {'fcntl': fake}):
            with self.assertRaises(OSError):
                with driver.run_lease(self.root, backend=driver._FcntlLease):
                    self.fail('the body must not run')

    def test_the_backend_follows_the_platform(self):
        self.assertIs(driver.lease_backend('nt'), driver._MsvcrtLease)
        self.assertIs(driver.lease_backend('posix'), driver._FcntlLease)
        self.assertIs(driver.lease_backend(), driver._MsvcrtLease if os.name == 'nt' else driver._FcntlLease)

    @unittest.skipIf(os.name == 'nt', 'fcntl.flock is POSIX-only: on Windows the msvcrt backend is the real lease (R1b controls); this body runs where fcntl exists, e.g. in the compute image')
    def test_real_flock_conflicts_between_two_open_file_descriptions_in_one_process(self):
        with driver.run_lease(self.root, backend=driver._FcntlLease):
            with self.assertRaises(SystemExit):
                with driver.run_lease(self.root, backend=driver._FcntlLease):
                    self.fail('a second holder must not run')
        with driver.run_lease(self.root, backend=driver._FcntlLease):
            pass


if __name__ == '__main__':
    unittest.main()
