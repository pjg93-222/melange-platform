"""Inert R1b controls for the owner rulings layered on the D073 base: key-scoped 429 returns to pending, the run lease behind a function,
the append-only endpoint-contract re-bind, the new-ledger refusal, worker cap/default, and the reader's consumer exclusion rule.

No credentials, provider or network: every ledger, archive and state directory is a temporary directory; the real accounted transport only
receives a scripted fake opener; sockets and the key store are refused by the overlay guard.
"""
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

import test_collect_committee_activity_concurrency as cc
import test_collect_committee_activity_endpoints as ep

driver, accounted, contracts = cc.driver, cc.accounted, ep.contracts
F_ID, H4_ID = ep.F_ID, ep.H4_ID
TOTALS_ID = contracts.binding('totals')['contract_id']


def http(code):
    return HTTPError('https://inert.example/', code, 'inert', {}, None)


def fmt(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='R1b-inert-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)


# ---------------------------------------------------------------- ruling: a 429'd endpoint returns to pending immediately
class Ruling429(Temp):
    def transport(self, opener, keys=cc.KEYS[:3]):
        transport = accounted.AccountedTransport(list(keys), self.root / 'quota.sqlite', self.root / 'archive', 5000, opener=opener, cap=1000)
        self.addCleanup(transport.close)
        return transport

    def test_status_classes(self):
        self.assertEqual(driver._status(http(429)), 'pending')
        self.assertEqual(driver._status(http(503)), 'blocked')
        self.assertEqual(driver._status(http(404)), 'source_refused')
        self.assertEqual(driver._status(URLError('x')), 'blocked')
        self.assertEqual(driver._status(ValueError('x')), 'inconsistent')
        self.assertEqual(driver._status(accounted.FECAdmissionRefused('x')), 'pending')
        self.assertTrue(driver._key_scoped(http(429)))
        self.assertFalse(driver._is_global(http(429)), 'a 429 is never a global stop')
        self.assertTrue(driver._is_global(http(401)) and driver._is_global(http(403)))

    def test_a_429_commit_leaves_the_endpoint_pending_with_its_failure_row_and_archive_pair(self):
        frontier = driver.Frontier(self.root / 'frontier.sqlite', include_later_cycle_context=False)
        self.addCleanup(frontier.close)
        frontier.seed(cc.SMALL_SEEDS)
        opener = cc.ScriptedOpener(cc.World(failures=False), key_responses={cc.KEYS[0]: (b'{}', 429), cc.KEYS[1]: (b'{}', 429), cc.KEYS[2]: (b'{}', 429)})
        transport = self.transport(opener)
        row = frontier.db.execute("SELECT * FROM endpoints WHERE kind='B' ORDER BY committee_id LIMIT 1").fetchone()
        job = frontier._prepare(row)
        outcome = driver._fetch(transport, job, threading.Event(), True)
        self.assertEqual((type(outcome.error).__name__, outcome.error.code, outcome.attempts), ('HTTPError', 429, 1))
        failed = frontier._commit(job, outcome)
        self.assertEqual(failed.error.code, 429)
        current = frontier.db.execute('SELECT status,retry_after,attempt_count,last_error FROM endpoints WHERE committee_id=? AND cycle=? AND kind=?', job.key).fetchone()
        self.assertEqual((current['status'], current['retry_after'], current['attempt_count']), ('pending', None, 1), 'back in the queue at once, not parked with a back-off')
        failure = frontier.db.execute('SELECT error_type,archive_record_json FROM failures').fetchone()
        self.assertEqual(failure['error_type'], 'HTTPError')
        record = json.loads(failure['archive_record_json'])
        self.assertEqual(record['status'], 429)
        self.assertTrue(Path(record['path']).is_file() and Path(record['receipt_path']).is_file(), 'the archive pair is still written')
        self.assertIn(job.key, [(r['committee_id'], r['cycle'], r['kind']) for r in frontier._next_rows(50, set())], 'selectable again immediately')

    def test_another_key_serves_the_429d_endpoint_and_nothing_stays_parked(self):
        frontier = driver.Frontier(self.root / 'frontier.sqlite', include_later_cycle_context=True)
        self.addCleanup(frontier.close)
        frontier.seed(cc.SMALL_SEEDS)
        opener = cc.ScriptedOpener(cc.World(failures=False), key_responses={cc.KEYS[0]: (b'{}', 429)})
        report = frontier.run(self.transport(opener), max_failed_attempts=1, workers=1)
        self.assertIsNone(report['stop'])
        self.assertEqual(report['rate_limited_responses'], 1)
        limited = [call for call in opener.calls if call[1] == cc.KEYS[0]]
        self.assertEqual(len(limited), 1, 'the 429 key cools until its window resets')
        first_key = limited[0][0]
        served = [call[1] for call in opener.calls if call[0] == first_key]
        self.assertGreaterEqual(len(served), 2)
        self.assertEqual(served[0], cc.KEYS[0])
        self.assertNotIn(cc.KEYS[0], served[1:], 'a different key finished the endpoint')
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM endpoints WHERE status IN('blocked','pending')").fetchone()[0], 0)


# ---------------------------------------------------------------- ruling: worker cap 32, CLI default 1, per-key concurrency 2
class Workers(Temp):
    def test_cap_default_and_per_key_concurrency(self):
        self.assertEqual(driver.MAX_WORKERS, 32)
        transport = accounted.AccountedTransport([cc.KEY], self.root / 'q.sqlite', self.root / 'a', 5, opener=cc.ScriptedOpener(), cap=10)
        self.addCleanup(transport.close)
        self.assertEqual(transport.pool.per_key, 2)
        for workers in (0, 33):
            with self.subTest(workers=workers), mock.patch.object(sys, 'argv', ['collect', '--state', str(self.root / 's'), '--workers', str(workers)]):
                with self.assertRaises(SystemExit), mock.patch('sys.stderr', new=io.StringIO()):
                    driver.main()
        frontier = driver.Frontier(self.root / 'f.sqlite')
        self.addCleanup(frontier.close)
        with self.assertRaises(ValueError):
            frontier.run(mock.Mock(supports_concurrent_fetch=True), workers=33)

    def test_the_cli_default_is_one_worker_and_the_start_record_says_so(self):
        state, seeds = self.root / 'state', self.root / 'seeds.json'
        seeds.write_text(json.dumps({'schema_version': 1, 'seed_units': [{'committee_id': cc.cid(1), 'cycle': 2026}]}))
        keep = []

        def live(archive, maximum, **options):
            transport = accounted.AccountedTransport(list(cc.KEYS[:2]), self.root / 'quota.sqlite', archive, maximum, opener=cc.ScriptedOpener(cc.World(failures=False)), cap=1000)
            keep.append(transport)
            return transport
        out = io.StringIO()
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=live), redirect_stdout(out):
            with mock.patch.object(sys, 'argv', ['collect', '--state', str(state), '--seeds', str(seeds), '--max-requests', '3', '--live', '--init-new-ledger']):
                driver.main()
        start = json.loads(next(state.glob('RUN-*.START.json')).read_text())
        receipt = json.loads(next(state.glob('RECEIPT-*.json')).read_text())
        self.assertEqual((start['workers'], receipt['workers'], receipt['peak_in_flight']), (1, 1, 1))


# ---------------------------------------------------------------- ruling: the run lease behind a function (msvcrt kept; D075 implemented the fcntl backend R1b stubbed)
class Lease(Temp):
    def test_second_holder_is_refused_and_release_frees_the_lease(self):
        with driver.run_lease(self.root):
            with self.assertRaises(SystemExit) as caught:
                with driver.run_lease(self.root):
                    pass
            self.assertIn('run lease', str(caught.exception))
        with driver.run_lease(self.root):
            pass

    def test_backends_are_selected_by_platform_and_fcntl_is_a_real_backend(self):
        # D075 changed this control: R1b asserted that the fcntl backend raised NotImplementedError (a stub). It is now the flock backend of the container runtime; its behaviour
        # (non-blocking exclusive flock, refusal of a second holder, release, errors not swallowed) is pinned in test_d075_collector.Lease with a fake fcntl module, and with the real
        # flock where fcntl exists.
        self.assertIs(driver.lease_backend('nt'), driver._MsvcrtLease)
        self.assertIs(driver.lease_backend('posix'), driver._FcntlLease)
        self.assertIs(driver.lease_backend(), driver._MsvcrtLease if sys.platform == 'win32' else driver._FcntlLease)
        for backend in (driver._MsvcrtLease, driver._FcntlLease):
            self.assertTrue(callable(backend.acquire) and callable(backend.release))
        if sys.platform == 'win32':
            with self.assertRaises(ImportError):                  # fcntl does not exist on Windows: choosing the posix backend there fails loudly, never silently
                with driver.run_lease(self.root, backend=driver.lease_backend('posix')):
                    pass

    def test_an_injected_backend_that_cannot_lock_refuses_before_the_body_runs(self):
        class Busy:
            @staticmethod
            def acquire(stream):
                return False

            @staticmethod
            def release(stream):
                raise AssertionError('never acquired, never released')
        ran = []
        with self.assertRaises(SystemExit):
            with driver.run_lease(self.root, backend=Busy):
                ran.append(1)
        self.assertEqual(ran, [])


# ---------------------------------------------------------------- ruling: append-only endpoint-contract intent re-bind
class Rebind(Temp):
    def ledger(self, ids, rebind=False, units=((cc.cid(1), 2024), (cc.cid(2), 2024))):
        frontier = driver.Frontier(self.root / 'frontier.sqlite', enabled_endpoint_contracts=ids, rebind_endpoint_contracts=rebind)
        if units and not frontier.db.execute('SELECT 1 FROM units').fetchone():
            frontier.seed([{'committee_id': c, 'cycle': y, 'origins': ['industryA']} for c, y in units])
        return frontier

    def intent(self, version=1):
        return contracts.intent_path(self.root / 'frontier.sqlite', version)

    def test_a_changed_set_is_refused_without_the_flag_and_appended_as_a_superset_version_with_it(self):
        self.ledger([F_ID]).close()
        version1 = self.intent(1).read_bytes()
        with self.assertRaises(contracts.EndpointContractRefused) as caught:
            self.ledger([F_ID, H4_ID]).close()
        self.assertIn('--rebind-endpoint-contracts', str(caught.exception))
        self.assertFalse(self.intent(2).exists(), 'a refusal writes nothing')
        frontier = self.ledger([F_ID, H4_ID], rebind=True)
        self.addCleanup(frontier.close)
        enabled = frontier.contract_enablement
        self.assertEqual((enabled['binding']['version'], enabled['rebind']['new_version'], enabled['rebind']['added_contract_ids']), (2, 2, [H4_ID]))
        self.assertEqual(self.intent(1).read_bytes(), version1, 'the prior intent is kept byte for byte')
        document = json.loads(self.intent(2).read_bytes())
        self.assertEqual(document['enabled_contract_ids'], sorted([F_ID, H4_ID]))
        self.assertEqual(document['supersedes']['sha256'], hashlib.sha256(version1).hexdigest())
        self.assertEqual(document['module_sha256'], hashlib.sha256(Path(contracts.__file__).read_bytes()).hexdigest())
        self.assertEqual(enabled['binding']['path'], str(self.intent(2)))
        self.assertEqual([item['version'] for item in enabled['binding']['chain']], [1, 2])
        frontier.close()
        again = self.ledger([F_ID, H4_ID])    # an ordinary restart with the new set needs no flag
        self.addCleanup(again.close)
        self.assertEqual(again.contract_enablement['binding']['version'], 2)
        self.assertNotIn('rebind', again.contract_enablement)

    def test_removal_is_never_allowed_even_with_the_flag(self):
        self.ledger([F_ID, H4_ID]).close()
        with self.assertRaises(contracts.EndpointContractRefused):     # the collector's own check (active H4 state lacks its contract) refuses first
            self.ledger([F_ID], rebind=True).close()
        frontier = self.ledger([F_ID, H4_ID])
        self.addCleanup(frontier.close)
        with self.assertRaises(contracts.EndpointContractRefused) as caught:      # and the contract module refuses a shrinking set on its own
            with frontier.transaction():
                contracts.bind_enablement(frontier.db, frontier.path, [F_ID], rebind=True)
        self.assertIn('never allowed', str(caught.exception))
        with self.assertRaises(contracts.EndpointContractRefused) as caught:      # an empty set can never be a re-bind
            with frontier.transaction():
                contracts.bind_enablement(frontier.db, frontier.path, [], rebind=True)
        self.assertIn('full enabled ID set', str(caught.exception))
        frontier.close()
        with self.assertRaises(contracts.EndpointContractRefused):
            self.ledger([F_ID]).close()
        with self.assertRaises(contracts.EndpointContractRefused):
            self.ledger([], rebind=True).close()
        self.assertFalse(self.intent(2).exists())

    def test_a_rebind_that_changes_nothing_writes_nothing_and_needs_a_prior_intent(self):
        with self.assertRaises(contracts.EndpointContractRefused) as caught:
            self.ledger([F_ID], rebind=True).close()
        self.assertIn('Nothing to re-bind', str(caught.exception))
        self.assertFalse(self.intent(1).exists(), 'a re-bind never creates the first intent')
        self.ledger([F_ID]).close()
        frontier = self.ledger([F_ID], rebind=True)
        self.addCleanup(frontier.close)
        self.assertFalse(self.intent(2).exists())
        self.assertNotIn('rebind', frontier.contract_enablement)

    def test_a_changed_module_hash_alone_is_rebound_and_the_old_intent_stays(self):
        self.ledger([F_ID]).close()
        old = json.loads(self.intent(1).read_bytes())
        old['module_sha256'] = '0' * 64                      # an intent written by an earlier module version
        self.intent(1).write_bytes(fmt(old))
        stale = self.intent(1).read_bytes()
        with self.assertRaises(contracts.EndpointContractRefused):
            self.ledger([F_ID]).close()
        frontier = self.ledger([F_ID], rebind=True)
        self.addCleanup(frontier.close)
        self.assertTrue(frontier.contract_enablement['rebind']['module_sha256_changed'])
        self.assertEqual(self.intent(1).read_bytes(), stale)
        new = json.loads(self.intent(2).read_bytes())
        self.assertEqual((new['supersedes']['module_sha256'], new['module_sha256']), ('0' * 64, hashlib.sha256(Path(contracts.__file__).read_bytes()).hexdigest()))

    def test_a_tampered_or_gapped_chain_is_refused(self):
        self.ledger([F_ID]).close()
        self.ledger([F_ID, H4_ID], rebind=True).close()
        good = self.intent(1).read_bytes()
        document = json.loads(good)
        document['purpose'] = 'edited'
        self.intent(1).write_bytes(fmt(document))
        with self.assertRaises(contracts.EndpointContractRefused):
            self.ledger([F_ID, H4_ID]).close()
        with self.assertRaises(contracts.EndpointContractRefused):      # nor may a further re-bind extend the edited chain
            self.ledger([F_ID, H4_ID, TOTALS_ID], rebind=True).close()
        self.assertFalse(self.intent(3).exists())
        self.intent(1).write_bytes(good)
        self.ledger([F_ID, H4_ID]).close()
        self.intent(4).write_bytes(self.intent(2).read_bytes())
        with self.assertRaises(contracts.EndpointContractRefused):
            self.ledger([F_ID, H4_ID]).close()

    def test_totals_first_then_f_and_h4_needs_the_rebind_and_promotes_only_the_added_rows(self):
        self.ledger([]).close()                      # units exist before totals is ever enabled
        first = self.ledger([TOTALS_ID])
        self.assertEqual(first.contract_enablement['created_existing_endpoints'], 2)
        first.close()
        with self.assertRaises(contracts.EndpointContractRefused):
            self.ledger([TOTALS_ID, F_ID]).close()
        second = self.ledger([TOTALS_ID, F_ID], rebind=True)
        self.addCleanup(second.close)
        self.assertEqual((second.contract_enablement['activated_existing_endpoints'], second.contract_enablement['created_existing_endpoints']), (2, 0))
        self.assertEqual(sorted(second.db.execute("SELECT DISTINCT status FROM endpoints WHERE kind='H4'").fetchall())[0][0], 'unsupported')

    def test_the_cli_rebind_is_offline_and_refuses_unsafe_combinations(self):
        state = self.root / 'state'
        driver.Frontier(state / 'frontier.sqlite', enabled_endpoint_contracts=[F_ID]).close()
        base = ['collect', '--state', str(state)]
        for flags in (['--rebind-endpoint-contracts'], ['--rebind-endpoint-contracts', '--enable-endpoint-contract', F_ID, '--live'],
                      ['--rebind-endpoint-contracts', '--recover-crashed-runs', '--enable-endpoint-contract', F_ID],
                      ['--rebind-endpoint-contracts', '--enable-endpoint-contract', F_ID, '--workers', '4']):
            with self.subTest(flags=flags), mock.patch.object(sys, 'argv', base + flags), mock.patch('sys.stderr', new=io.StringIO()):
                with self.assertRaises(SystemExit):
                    driver.main()
        out = io.StringIO()
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=AssertionError('a re-bind makes no provider call')), redirect_stdout(out):
            with mock.patch.object(sys, 'argv', base + ['--rebind-endpoint-contracts', '--enable-endpoint-contract', F_ID, '--enable-endpoint-contract', H4_ID]):
                driver.main()
        printed = json.loads(out.getvalue())
        self.assertEqual((printed['binding']['version'], printed['rebind']['added_contract_ids']), (2, [H4_ID]))
        self.assertEqual(list(state.glob('RECEIPT-*')) + list(state.glob('RUN-*')) + list(state.glob('responses-*')), [])

    def test_a_rebind_is_refused_while_a_run_is_unresolved(self):
        state = self.root / 'state'
        driver.Frontier(state / 'frontier.sqlite', enabled_endpoint_contracts=[F_ID]).close()
        (state / ('responses-' + '0' * 32)).mkdir()
        with mock.patch.object(sys, 'argv', ['collect', '--state', str(state), '--rebind-endpoint-contracts', '--enable-endpoint-contract', F_ID, '--enable-endpoint-contract', H4_ID]):
            with self.assertRaises(SystemExit) as caught:
                driver.main()
        self.assertIn('Unresolved', str(caught.exception))
        self.assertFalse((state / 'frontier.endpoint-contracts.v2.json').exists())


# ---------------------------------------------------------------- ruling: no frontier.sqlite -> refuse unless --init-new-ledger
class NewLedger(Temp):
    def run_main(self, state, extra, seeds=None):
        seeds = seeds or self.root / 'seeds.json'
        if not seeds.exists():
            seeds.write_text(json.dumps({'schema_version': 1, 'seed_units': [{'committee_id': cc.cid(1), 'cycle': 2026}]}))

        def live(archive, maximum, **options):
            return accounted.AccountedTransport(list(cc.KEYS[:2]), self.root / 'quota.sqlite', archive, maximum, opener=cc.ScriptedOpener(cc.World(failures=False)), cap=1000)
        out = io.StringIO()
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=live), redirect_stdout(out):
            with mock.patch.object(sys, 'argv', ['collect', '--state', str(state), '--seeds', str(seeds), '--max-requests', '2'] + extra):
                driver.main()
        return json.loads(out.getvalue().strip().splitlines()[-1])

    def test_a_missing_ledger_is_refused_and_nothing_at_all_is_written(self):
        state = self.root / 'fresh-state'
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=AssertionError('refused before any transport')):
            with mock.patch.object(sys, 'argv', ['collect', '--state', str(state), '--seeds', str(self.root / 'nothing.json'), '--max-requests', '2', '--live']):
                with self.assertRaises(SystemExit) as caught:
                    driver.main()
        self.assertIn('--init-new-ledger', str(caught.exception))
        self.assertFalse(state.exists(), 'no state directory, lease, seed copy or START was created')
        # a state directory that exists but holds no ledger is refused the same way and stays empty
        state.mkdir()
        with self.assertRaises(SystemExit):
            self.run_main(state, ['--live'])
        self.assertEqual(list(state.iterdir()), [])

    def test_the_flag_creates_the_ledger_once_and_an_existing_ledger_refuses_it(self):
        state = self.root / 'state'
        printed = self.run_main(state, ['--live', '--init-new-ledger'])
        self.assertTrue((state / 'frontier.sqlite').is_file() and Path(printed['receipt']).is_file())
        start = json.loads(next(state.glob('RUN-*.START.json')).read_text())
        self.assertTrue(start['init_new_ledger'])
        with self.assertRaises(SystemExit) as caught:
            self.run_main(state, ['--live', '--init-new-ledger'])
        self.assertIn('exists', str(caught.exception))
        again = self.run_main(state, ['--live'])        # an ordinary restart needs nothing
        self.assertTrue(Path(again['receipt']).is_file())

    def test_the_offline_subcommands_never_create_a_ledger(self):
        state = self.root / 'fresh-state'
        for flags in (['--recover-crashed-runs'], ['--rebind-endpoint-contracts', '--enable-endpoint-contract', F_ID],
                      ['--close-out-legacy-run', str(state / ('responses-' + 'a' * 32)), '--baseline-snapshot', 'x', '--baseline-receipt', 'y', '--launched', 'z']):
            with self.subTest(flags=flags), mock.patch.object(sys, 'argv', ['collect', '--state', str(state)] + flags):
                with self.assertRaises(SystemExit):
                    driver.main()
            self.assertFalse(state.exists())
        for flags in (['--recover-crashed-runs', '--init-new-ledger'],):
            with self.subTest(flags=flags), mock.patch.object(sys, 'argv', ['collect', '--state', str(state)] + flags), mock.patch('sys.stderr', new=io.StringIO()):
                with self.assertRaises(SystemExit):
                    driver.main()
        self.assertFalse(state.exists())


# ---------------------------------------------------------------- ruling: campaign_priority / fec_bulk_oth origins are not giving evidence
class ConsumerRule(unittest.TestCase):
    def test_the_reader_docstring_carries_the_exclusion_rule(self):
        import committee_activity_reader as reader
        text = reader.__doc__
        for phrase in ('giving_or_payment_evidence', 'campaign_priority', 'fec_bulk_oth', 'hint_not_coverage', 'is False', 'origin_catalog'):
            self.assertIn(phrase, text)

    def test_priority_origins_are_tagged_not_giving_evidence(self):
        with tempfile.TemporaryDirectory(prefix='R1b-inert-') as tmp:
            frontier = driver.Frontier(Path(tmp) / 'frontier.sqlite')
            try:
                ccl = Path(tmp) / 'ccl.txt'
                ccl.write_text('H0AA00003|2024|2024|C00000009|H|P|1\n', encoding='utf-8')
                frontier.set_priority([('H0AA00003', 2024)], ccl={2024: {'path': ccl, 'vintage': 'inert'}})
                base = json.loads(frontier.db.execute("SELECT base_metadata_json FROM origin_catalog WHERE origin_id='campaign-priority-H0AA00003-2024'").fetchone()[0])
            finally:
                frontier.close()
        self.assertIs(base['giving_or_payment_evidence'], False)
        self.assertIs(base['hint_not_coverage'], True)
        self.assertEqual(base['discovery_source'], 'campaign_priority')


# ---------------------------------------------------------------- review M1: after a global stop nobody still waiting for a key slot may send a request
class Always429(cc.World):
    """Endpoint (C1, 2024, B) answers 429 to every request from every key; everything else is the normal inert world."""
    def special(self, endpoint, query):
        return (b'{}', 429) if self.parse(endpoint, query) == (1, 2024, 'B') else None


class GlobalStop(Temp):
    def test_a_401_stops_the_pool_before_its_key_slot_is_released_so_a_waiting_caller_is_refused(self):
        def hook(number, key, query):
            time.sleep(0.6 if number == 2 else 0.05 if number == 1 else 0)
        opener = cc.ScriptedOpener(cc.World(failures=False), overrides={1: (b'{}', 401)}, hook=hook)
        transport = accounted.AccountedTransport([cc.KEY], self.root / 'quota.sqlite', self.root / 'archive', 50, opener=opener, cap=100)
        self.addCleanup(transport.close)
        results = {}

        def call(name, committee, delay):
            time.sleep(delay)
            try:
                transport.fetch('schedules/schedule_b/', {'committee_id': cc.cid(committee), 'per_page': 100, 'two_year_transaction_period': 2024}, call={})
                results[name] = 'ok'
            except Exception as error:
                results[name] = (type(error).__name__, getattr(error, 'code', None))
        threads = [threading.Thread(target=call, args=item) for item in (('A', 1, 0), ('B', 2, 0.005), ('C', 3, 0.03))]   # A and B take the key's two slots; C waits
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(results, {'A': ('HTTPError', 401), 'B': 'ok', 'C': ('FECAdmissionRefused', None)})
        self.assertEqual((transport.http_attempts, len(opener.calls)), (2, 2), 'the waiting caller never sent a request')

    def test_one_key_eight_workers_and_a_401_on_the_first_response_send_at_most_one_extra_request(self):
        frontier = driver.Frontier(self.root / 'f.sqlite', include_later_cycle_context=True)
        self.addCleanup(frontier.close)
        frontier.seed(cc.SEEDS)
        opener = cc.ScriptedOpener(cc.World(failures=False), delay=0.08, overrides={1: (b'{}', 401)})
        transport = accounted.AccountedTransport([cc.KEY], self.root / 'quota.sqlite', self.root / 'archive', 5000, opener=opener, cap=1000)
        self.addCleanup(transport.close)
        report = frontier.run(transport, workers=8, max_failed_attempts=100)
        slots = transport.pool.per_key
        self.assertLessEqual(transport.http_attempts, slots + 1, 'the two key slots were already in flight; at most one more request, not seven')
        self.assertEqual((report['stop']['error_type'], '401' in report['stop']['detail']), ('HTTPError', True), 'the first cause is kept')
        self.assertEqual(report['attempts_recorded'], transport.http_attempts)
        self.assertEqual(frontier.db.execute('SELECT COALESCE(SUM(attempt_count),0) FROM endpoints').fetchone()[0], transport.http_attempts)
        refused = frontier.db.execute("SELECT COUNT(*) FROM failures WHERE error_type='FECAdmissionRefused'").fetchone()[0]
        self.assertGreaterEqual(refused, 1, 'the released jobs are recorded, with 0 attempts')
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM endpoints WHERE status='blocked'").fetchone()[0], 1, 'only the endpoint that received the 401')
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM failures f JOIN endpoints e ON e.committee_id=f.committee_id AND e.cycle=f.cycle AND e.kind=f.kind WHERE f.error_type='FECAdmissionRefused' AND e.status!='pending'").fetchone()[0], 0, 'released jobs stay pending')


    def test_the_failure_allowance_stop_also_releases_the_jobs_still_waiting(self):
        frontier = driver.Frontier(self.root / 'f.sqlite', include_later_cycle_context=True)
        self.addCleanup(frontier.close)
        frontier.seed(cc.SEEDS)
        opener = cc.ScriptedOpener(cc.World(failures=False), delay=1.0, key_responses={cc.KEY: (b'{}', 503)})      # slow enough that the writer reaction (commit + stop) is well inside one request
        transport = accounted.AccountedTransport([cc.KEY], self.root / 'quota.sqlite', self.root / 'archive', 5000, opener=opener, cap=1000)
        self.addCleanup(transport.close)
        report = frontier.run(transport, workers=8, max_failed_attempts=1)
        slots = transport.pool.per_key
        self.assertIsNotNone(report['stop'])
        self.assertLessEqual(transport.http_attempts, 2 * slots, 'the first completions may admit one waiter per slot before the writer reacts; after that nobody waiting sends')
        self.assertEqual(report['attempts_recorded'], transport.http_attempts)
        self.assertEqual(frontier.db.execute('SELECT COALESCE(SUM(attempt_count),0) FROM endpoints').fetchone()[0], transport.http_attempts)


# ---------------------------------------------------------------- review L1: a second 429 for one endpoint benches it for the run
class BenchedEndpoint(Temp):
    def test_an_endpoint_that_always_gets_a_429_is_benched_after_the_second_and_the_run_carries_on(self):
        frontier = driver.Frontier(self.root / 'f.sqlite', include_later_cycle_context=True)
        self.addCleanup(frontier.close)
        frontier.seed(cc.SEEDS)
        opener = cc.ScriptedOpener(Always429(failures=False), delay=0.01)
        transport = accounted.AccountedTransport(list(cc.KEYS), self.root / 'quota.sqlite', self.root / 'archive', 5000, opener=opener, cap=1000)
        self.addCleanup(transport.close)
        report = frontier.run(transport, workers=2, max_failed_attempts=100)
        self.assertIsNone(report['stop'], 'one endpoint cannot end the run by cooling every key')
        self.assertEqual((report['rate_limited_responses'], report['rate_limited_endpoints_skipped']), (2, ['C00000001/2024/B']))
        hits = [call for call in opener.calls if call[0][0] == 'schedules/schedule_b/' and call[0][1] == cc.cid(1) and call[0][2] == '2024']
        self.assertEqual(len(hits), 2, 'asked twice, then left alone')
        self.assertEqual(len({call[1] for call in hits}), 2, 'by two different keys')
        self.assertEqual(sum(1 for until in transport.pool.cooldown.values() if until > time.time()), 2, 'only those two keys were cooled')
        row = frontier.db.execute("SELECT status,attempt_count,last_error,retry_after FROM endpoints WHERE committee_id=? AND cycle=2024 AND kind='B'", (cc.cid(1),)).fetchone()
        self.assertEqual((row['status'], row['attempt_count'], row['retry_after']), ('pending', 2, None))
        self.assertIn('429 twice in one run', row['last_error'])
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM endpoints WHERE status IN('pending','blocked')").fetchone()[0], 1, 'every other endpoint was served')
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM failures WHERE detail LIKE '%429%' AND archive_record_json IS NOT NULL").fetchone()[0], 2)
        self.assertEqual((report['attempts_recorded'], transport.http_attempts), (len(opener.calls), len(opener.calls)))

    def test_the_bench_is_for_one_run_only_the_next_run_asks_again(self):
        frontier = driver.Frontier(self.root / 'f.sqlite', include_later_cycle_context=True)
        self.addCleanup(frontier.close)
        frontier.seed(cc.SEEDS)
        for number, world in enumerate((Always429(failures=False), cc.World(failures=False))):
            opener = cc.ScriptedOpener(world)
            transport = accounted.AccountedTransport(list(cc.KEYS), self.root / ('quota%d.sqlite' % number), self.root / ('archive%d' % number), 5000, opener=opener, cap=1000)
            self.addCleanup(transport.close)
            report = frontier.run(transport, workers=2, max_failed_attempts=100)
        self.assertEqual(report['rate_limited_endpoints_skipped'], [])
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM endpoints WHERE status IN('pending','blocked')").fetchone()[0], 0)
        self.assertIsNone(frontier.db.execute("SELECT last_error FROM endpoints WHERE committee_id=? AND cycle=2024 AND kind='B'", (cc.cid(1),)).fetchone()[0], 'the note clears with the first good page')


# ---------------------------------------------------------------- review L2: receipts, START records and ABANDONED markers are published atomically
class AtomicPublication(Temp):
    def test_write_new_publishes_a_complete_file_exclusively_or_nothing(self):
        path = self.root / 'RECEIPT-x.json'
        with mock.patch.object(driver.os, 'fsync', side_effect=OSError('inert disk failure')):
            with self.assertRaises(OSError):
                driver.write_new(path, {'a': 1})
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [], 'the failed attempt left neither a final file nor a partial one')
        driver.write_new(path, {'a': 1})
        self.assertEqual(json.loads(path.read_text()), {'a': 1})
        self.assertEqual([item.name for item in self.root.iterdir()], [path.name])
        with self.assertRaises(FileExistsError):
            driver.write_new(path, {'a': 2})
        self.assertEqual(json.loads(path.read_text()), {'a': 1}, 'an existing file is never replaced')

    def test_a_partial_file_that_cannot_be_removed_after_the_publish_is_not_a_failure(self):      # delta review D1: e.g. a sharing violation from a scanner or sync engine
        path = self.root / 'RECEIPT-x.json'
        real = Path.unlink

        def sharing_violation(self, *args, **options):
            if '.partial-' in self.name:
                raise PermissionError(32, 'inert sharing violation')
            return real(self, *args, **options)
        with mock.patch.object(Path, 'unlink', sharing_violation):
            driver.write_new(path, {'a': 1})          # must not raise: the final name is already published
        self.assertEqual(json.loads(path.read_text()), {'a': 1})
        stray = [item.name for item in self.root.iterdir() if item.name != path.name]
        self.assertEqual(len(stray), 1)
        self.assertIn('.partial-', stray[0])
        with mock.patch.object(Path, 'unlink', sharing_violation):
            with self.assertRaises(FileExistsError):      # the real cause still surfaces, not the cleanup error
                driver.write_new(path, {'a': 2})
        self.assertEqual(json.loads(path.read_text()), {'a': 1}, 'an existing file is never replaced')

    def test_a_write_cut_short_by_json_never_shows_a_truncated_file_under_the_final_name(self):
        path = self.root / 'RUN-x.START.json'

        def half(document, stream, **options):
            stream.write('{"half": ')
            raise KeyboardInterrupt('inert interrupt mid-write')
        with mock.patch.object(driver.json, 'dump', half):
            with self.assertRaises(KeyboardInterrupt):
                driver.write_new(path, {'a': 1})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_process_killed_before_the_final_name_appears_leaves_the_run_unresolved(self):
        state = self.root / 'state'
        state.mkdir()
        run = 'a' * 32
        driver.write_new(state / ('RUN-' + run + '.START.json'), {'schema_version': 1, 'run_id': run})
        (state / ('responses-' + run)).mkdir()
        code = ('import os,sys\nsys.path.insert(0,sys.argv[1])\nimport collect_committee_activity as d\n'
                'd.os.link=lambda *a,**k:os._exit(7)\nd.write_new(sys.argv[2],{"receipt":"complete"})\n')
        done = subprocess.run([sys.executable, '-B', '-c', code, str(Path(driver.__file__).parent), str(state / ('RECEIPT-' + run + '.json'))], capture_output=True)
        self.assertEqual(done.returncode, 7)
        self.assertFalse((state / ('RECEIPT-' + run + '.json')).exists(), 'no truncated receipt under the final name')
        self.assertEqual(len(list(state.glob('RECEIPT-*.partial-*'))), 1, 'a hard kill leaves only an ignorable partial file')
        self.assertEqual(driver.unresolved_runs(state), ([run], []), 'a partial file does not resolve the run')
        self.assertEqual(list(state.glob('RECEIPT-*.json')), [])

    def test_a_receipt_that_cannot_be_published_leaves_the_run_unresolved_and_blocks_the_next_start(self):
        state, seeds = self.root / 'state', self.root / 'seeds.json'
        seeds.write_text(json.dumps({'schema_version': 1, 'seed_units': [{'committee_id': cc.cid(1), 'cycle': 2026}]}))
        real = driver.write_new

        def flaky(path, document):
            if Path(path).name.startswith('RECEIPT-'):
                raise OSError('inert disk failure')
            return real(path, document)

        def live(archive, maximum, **options):
            return accounted.AccountedTransport(list(cc.KEYS[:2]), self.root / 'quota.sqlite', archive, maximum, opener=cc.ScriptedOpener(cc.World(failures=False)), cap=1000)
        argv = ['collect', '--state', str(state), '--seeds', str(seeds), '--max-requests', '2', '--live', '--init-new-ledger']
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=live), mock.patch.object(driver, 'write_new', flaky), redirect_stdout(io.StringIO()):
            with mock.patch.object(sys, 'argv', argv):
                with self.assertRaises(OSError):
                    driver.main()
        self.assertEqual(list(state.glob('RECEIPT-*')), [])
        crashed, legacy = driver.unresolved_runs(state)
        self.assertEqual((len(crashed), legacy), (1, []))
        with mock.patch.object(sys, 'argv', argv[:-1]):
            with self.assertRaises(SystemExit) as caught:
                driver.main()
        self.assertIn('Unresolved collector run', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
