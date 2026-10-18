"""Inert R1b controls for `--close-out-legacy-run`: the offline close-out of a pre-D071 collector run (no START record, no receipt).

A synthetic schema-2 baseline snapshot plus a synthetic legacy run (pages, a failure with its own pair, one complete pair the ledger never
committed, one body without metadata) stand in for accepted batch N-1 and the crashed batch N. No provider, credential or network: the tool
makes none and the overlay guard would stop it. Real accepted batch-02 data is exercised by rehearse_closeout_batch02.py, not here.
"""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import test_collect_committee_activity_concurrency as cc

driver, accounted = cc.driver, cc.accounted
PREVIOUS, RUN, OTHER = '1' * 32, 'b' * 32, 'c' * 32


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tree(root):
    """Every file under root with its size and (for small files) hash: what 'nothing was changed' means."""
    out = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_file():
            out[str(path.relative_to(root))] = (path.stat().st_size, sha(path.read_bytes()) if path.stat().st_size < 50_000_000 else None)
    return out


def pair(folder, attempt, endpoint, query, status=200, body=None):
    folder.mkdir(exist_ok=True)
    body = body if body is not None else json.dumps({'results': [], 'pagination': {'count': 0, 'is_count_exact': True, 'pages': 1, 'per_page': 100, 'last_indexes': {}}, 'n': attempt}).encode()
    digest = sha(body)
    name = 'attempt-%06d-%s' % (attempt, digest[:12])
    path = folder / (name + '.body.bin')
    path.write_bytes(body)
    record = {'schema_version': 1, 'source_kind': 'fec-api-original-http-body', 'attempt': attempt, 'captured_utc': '2026-10-15T00:00:00+00:00', 'endpoint': endpoint,
              'query': query, 'status': status, 'sha256': digest, 'bytes': len(body), 'original_response_bytes': True, 'headers': {}, 'path': str(path), 'raw_body_persisted': True}
    (folder / (name + '.json')).write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return path, folder / (name + '.json'), record


def b_query(committee, cycle):
    return {'committee_id': committee, 'per_page': 100, 'two_year_transaction_period': cycle}


class Legacy(unittest.TestCase):
    """state/ with accepted batch-01 (schema 2) and a crashed legacy run RUN whose ledger holds 3 pages and 1 failure; one more pair is an orphan."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='R1b-inert-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.build()

    def schema2(self, path):
        db = sqlite3.connect(path, isolation_level=None)
        db.executescript(driver.SCHEMA2)
        db.execute('PRAGMA user_version=2')
        return db

    def endpoints(self, db, committee, cycle):
        db.execute('INSERT INTO units VALUES(?,?,?)', (committee, cycle, 'x'))
        db.execute('INSERT INTO origins VALUES(?,?,?,?)', (committee, cycle, 'industryA', '{"first_reached_cycle":%d}' % cycle))
        for kind in driver.KINDS:
            db.execute('INSERT INTO endpoints(committee_id,cycle,kind,status) VALUES(?,?,?,?)', (committee, cycle, kind, 'pending' if kind in driver.SUPPORTED else 'unsupported'))

    def page(self, db, committee, cycle, kind, number, record, path):
        db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?,?,?)', (committee, cycle, kind, number, '{}', '{}', '{}', str(path), record['sha256'], record['bytes'], 0))
        db.execute("UPDATE endpoints SET status='complete',pages_stored=pages_stored+1,attempt_count=attempt_count+1 WHERE committee_id=? AND cycle=? AND kind=?", (committee, cycle, kind))

    def build(self):
        state = self.state
        c1, c2 = cc.cid(1), cc.cid(2)
        previous = state / ('responses-' + PREVIOUS)
        batch = state / 'accepted-batches' / 'batch-01'
        batch.mkdir(parents=True)
        seeds = state / 'seeds.json'
        seeds.write_text('{"schema_version":1,"seed_units":[]}')
        base = self.schema2(batch / 'frontier.sqlite')
        self.endpoints(base, c1, 2024)
        self.endpoints(base, c2, 2024)
        path, _, record = pair(previous, 1, 'schedules/schedule_b/', b_query(c1, 2024))
        self.page(base, c1, 2024, 'B', 1, record, path)
        for number in range(300):    # bulk so a rolled-back transaction spans many pages (hot-journal control)
            base.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (c1, 2024, 'B', 'o%d' % number, 1, number, str(number), 'r' * 2000, 'h', None, 'disbursement_amount', 'valid_decimal'))
        base.close()
        self.seed_receipt = {'path': str(seeds), 'sha256': sha(seeds.read_bytes()), 'bytes': seeds.stat().st_size, 'sha256_after': sha(seeds.read_bytes()), 'after_unchanged': True, 'captured_copy': str(seeds)}
        self.baseline_receipt = state / ('RECEIPT-' + PREVIOUS + '.json')
        self.baseline_receipt.write_text(json.dumps({'schema_version': 1, 'http_attempts': 1, 'seed_receipt': self.seed_receipt}), encoding='utf-8')
        self.snapshot = batch / 'SNAPSHOT.json'
        self.snapshot.write_text(json.dumps({'schema_version': 1, 'snapshot_sha256': sha((batch / 'frontier.sqlite').read_bytes()), 'later_cycle_context_explicitly_enabled': False,
                                             'original_collection_receipt': {'path': str(self.baseline_receipt), 'sha256': sha(self.baseline_receipt.read_bytes())}}), encoding='utf-8')
        shutil.copyfile(batch / 'frontier.sqlite', state / 'frontier.sqlite')
        live = sqlite3.connect(state / 'frontier.sqlite', isolation_level=None)
        folder = state / ('responses-' + RUN)
        for number, (committee, kind, endpoint) in enumerate(((c2, 'B', 'schedules/schedule_b/'), (c1, 'E_regular', 'schedules/schedule_e/'), (c2, 'E_regular', 'schedules/schedule_e/')), start=1):
            query = b_query(committee, 2024) if kind == 'B' else {'committee_id': committee, 'per_page': 100, 'cycle': 2024, 'is_notice': 'false'}
            path, _, record = pair(folder, number, endpoint, query)
            self.page(live, committee, 2024, kind, 1, record, path)
        path, _, record = pair(folder, 4, 'schedules/schedule_e/', {'committee_id': c1, 'per_page': 100, 'cycle': 2024, 'is_notice': 'true'}, status=503, body=b'{}')
        live.execute("UPDATE endpoints SET status='blocked',attempt_count=1,last_error='503' WHERE committee_id=? AND cycle=2024 AND kind='E_notice'", (c1,))
        live.execute('INSERT INTO failures(committee_id,cycle,kind,at,error_type,detail,request_query_json,archive_record_json) VALUES(?,?,?,?,?,?,?,?)',
                     (c1, 2024, 'E_notice', 'x', 'HTTPError', '503', '{}', json.dumps(record)))
        self.orphan = pair(folder, 5, 'schedules/schedule_b/', b_query(c1, 2026))
        (folder / 'attempt-000006-deadbeef0000.body.bin').write_bytes(b'half a body')
        live.close()
        self.folder = folder
        self.launched = state / 'LAUNCHED.json'
        self.launched.write_text(json.dumps({'actual_driver_argv': ['collect_committee_activity.py', '--state', str(state), '--seeds', str(seeds), '--max-requests', '20', '--max-failed-attempts', '8', '--live'],
                                             'physical_cap': 20, 'source_failure_allowance': 8, 'child_pid': 4_000_000, 'child_creation_filetime': 1}), encoding='utf-8')

    def close_out(self, **override):
        options = dict(baseline_snapshot=self.snapshot, baseline_receipt=self.baseline_receipt, launched=self.launched, state=self.state)
        options.update(override)
        return driver.close_out_legacy_run(self.folder, **options)

    def rows(self, table):
        db = sqlite3.connect(self.state / 'frontier.sqlite')
        try:
            return sorted(db.execute('SELECT * FROM ' + table).fetchall(), key=repr)
        finally:
            db.close()

    def refused(self, fragment, **override):
        before, ledger = tree(self.state), sha((self.state / 'frontier.sqlite').read_bytes())
        with self.assertRaises(driver.RecoveryRefused) as caught:
            self.close_out(**override)
        self.assertIn(fragment, str(caught.exception))
        self.assertEqual(tree(self.state), before, 'a refusal changes nothing')
        self.assertEqual(sha((self.state / 'frontier.sqlite').read_bytes()), ledger)


class CloseOut(Legacy):
    def test_the_orphan_pair_and_junk_are_quarantined_never_replayed_and_the_legacy_receipt_is_written(self):
        tables = {name: self.rows(name) for name in ('units', 'origins', 'endpoints', 'pages', 'observations', 'failures')}
        outcome = self.close_out()
        self.assertEqual({name: self.rows(name) for name in tables}, tables, 'no ledger row is written: nothing is replayed')
        receipt = json.loads(Path(outcome['receipt']).read_text())
        self.assertEqual((outcome['http_attempts'], outcome['new_pages'], outcome['quarantined'], outcome['hot_journal_found']), (4, 3, 3, False))
        self.assertEqual((receipt['http_attempts'], receipt['new_pages_this_invocation'], receipt['pages']), (4, 3, 4))
        self.assertTrue(receipt['recovered_after_crash'] and receipt['legacy_close_out'])
        self.assertEqual((receipt['stop']['error_type'], receipt['workers'], receipt['ledger_schema_version'], receipt['physical_request_cap'], receipt['failed_attempt_allowance']), ('CrashRecovered', 1, 2, 20, 8))
        self.assertEqual(receipt['seed_receipt'], self.seed_receipt, 'the chain keeps one seed receipt')
        self.assertEqual(receipt['ledger_sha256'], sha((self.state / 'frontier.sqlite').read_bytes()))
        recovery = receipt['recovery']
        self.assertEqual((recovery['replayed_pairs'], recovery['referenced_pairs'], recovery['synthesized_start']), (0, 4, {'pages': 1, 'sum_attempt_count': 1}))
        names = sorted(item['name'] for item in recovery['quarantined'])
        self.assertEqual(names, sorted([self.orphan[0].name, self.orphan[1].name, 'attempt-000006-deadbeef0000.body.bin']))
        moved = self.state / 'crash-quarantine' / RUN
        self.assertEqual(sorted(p.name for p in moved.iterdir()), names)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir() if p.name.endswith('.body.bin')), sorted([Path(r[7]).name for r in self.rows('pages') if RUN in r[7]] + [Path(json.loads(r[8])['path']).name for r in self.rows('failures')]))
        self.assertEqual(driver.unresolved_runs(self.state), ([], []), 'a normal --live start is no longer blocked by this folder')
        for entry in recovery['quarantined']:
            self.assertEqual(entry['sha256'], sha((moved / entry['name']).read_bytes()))

    def test_the_receipt_keeps_the_collector_receipt_shape(self):
        receipt = json.loads(Path(self.close_out()['receipt']).read_text())
        for key in ('schema_version', 'units', 'origins', 'pages', 'observations', 'edges', 'endpoint_status_counts', 'new_pages_this_invocation', 'stop', 'supported_outgoing_endpoint_kinds',
                    'endpoint_contract_enablement', 'later_cycle_context_explicitly_enabled', 'http_attempts', 'physical_request_cap', 'failed_attempt_allowance', 'raw_archive_directory',
                    'ledger_sha256', 'seed_receipt', 'exhaustive_disclosed_spending_complete', 'supported_outgoing_endpoints_complete'):
            self.assertIn(key, receipt)
        self.assertEqual((receipt['supported_outgoing_endpoint_kinds'], receipt['exhaustive_disclosed_spending_complete'], receipt['units'], receipt['origins']), (['B', 'E_notice', 'E_regular'], False, 2, 2))

    def test_the_first_open_is_read_write_so_a_hot_journal_rolls_back_and_the_ledger_is_restored_byte_for_byte(self):
        ledger = self.state / 'frontier.sqlite'
        original = sha(ledger.read_bytes())
        child = subprocess.Popen([sys.executable, '-B', '-c',
                                  "import sqlite3,sys,time\n"
                                  "db=sqlite3.connect(sys.argv[1],isolation_level=None,timeout=1)\n"
                                  "db.execute('PRAGMA cache_size=1')\n"
                                  "db.execute('BEGIN IMMEDIATE')\n"
                                  "db.execute(\"UPDATE observations SET raw_json=raw_json||'x'\")\n"
                                  "print('READY',flush=True)\n"
                                  "time.sleep(120)\n", str(ledger)], stdout=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline().strip(), b'READY')
        finally:
            child.kill()           # TerminateProcess: no rollback, the journal stays
            child.wait()
        journal = Path(str(ledger) + '-journal')
        self.assertTrue(journal.exists() and journal.stat().st_size > 0, 'the kill left a hot journal')
        self.assertNotEqual(sha(ledger.read_bytes()), original, 'the killed transaction had already modified the ledger file')
        outcome = self.close_out()
        receipt = json.loads(Path(outcome['receipt']).read_text())
        self.assertTrue(receipt['recovery']['hot_journal_found'] and receipt['recovery']['hot_journal_rolled_back'])
        self.assertFalse(journal.exists())
        self.assertEqual(receipt['ledger_sha256'], original, 'the rollback restored the pre-crash ledger exactly')
        self.assertEqual(receipt['http_attempts'], 4)

    def test_a_close_out_with_no_unreferenced_files_still_writes_the_receipt_and_a_rerun_is_refused(self):
        for item in list(self.orphan[:2]) + [self.folder / 'attempt-000006-deadbeef0000.body.bin']:
            item = Path(item) if not isinstance(item, Path) else item
            item.unlink()          # an interrupted earlier close-out already moved them away
        outcome = self.close_out()
        self.assertEqual(outcome['quarantined'], 0)
        self.assertFalse((self.state / 'crash-quarantine').exists())
        self.refused('already has a receipt')

    def test_refusals_leave_the_whole_state_unchanged(self):
        (self.state / ('RUN-' + RUN + '.START.json')).write_text('{}')
        self.refused('has a START record')
        (self.state / ('RUN-' + RUN + '.START.json')).unlink()
        (self.state / ('responses-' + OTHER)).mkdir()
        self.refused('Other unresolved run')
        (self.state / ('responses-' + OTHER)).rmdir()
        launch = json.loads(self.launched.read_text())
        self.launched.write_text(json.dumps({**launch, 'child_pid': os.getpid(), 'child_creation_filetime': None}))
        self.refused('still alive')
        self.launched.write_text(json.dumps(launch))
        self.launched.write_text(json.dumps({**launch, 'actual_driver_argv': launch['actual_driver_argv'] + ['--enable-endpoint-contract', 'x']}))
        self.refused('serial B/E run only')
        self.launched.write_text(json.dumps({**launch, 'physical_cap': 7}))
        self.refused('disagrees')
        self.launched.write_text(json.dumps({**launch, 'actual_driver_argv': launch['actual_driver_argv'] + ['--include-later-cycle-context']}))
        self.refused('later-cycle context')
        self.launched.write_text(json.dumps(launch))
        self.snapshot.write_text(self.snapshot.read_text().replace('"snapshot_sha256": "', '"snapshot_sha256": "0'))
        self.refused('does not hash')

    def test_a_baseline_receipt_the_snapshot_does_not_bind_is_refused(self):
        other = self.state / 'other-receipt.json'
        other.write_text(json.dumps({'seed_receipt': self.seed_receipt, 'edited': True}))
        self.refused('not the one the baseline snapshot binds', baseline_receipt=other)

    def test_two_complete_unreferenced_pairs_are_a_human_decision(self):
        pair(self.folder, 7, 'schedules/schedule_b/', b_query(cc.cid(1), 2022))
        self.refused('complete unreferenced pairs')

    def test_a_ledger_that_does_not_reconcile_with_the_folder_is_refused(self):
        db = sqlite3.connect(self.state / 'frontier.sqlite', isolation_level=None)      # a new page since the baseline whose archive is NOT in this folder
        db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?,?,?)', (cc.cid(1), 2024, 'E_notice', 1, '{}', '{}', '{}', str(self.state / ('responses-' + OTHER) / 'attempt-000001-x.body.bin'), 'x', 1, 0))
        db.close()
        self.refused('do not reconcile')

    def test_a_ledger_that_references_a_body_without_its_metadata_is_a_human_decision(self):
        body = sorted(p for p in self.folder.glob('attempt-000001-*.body.bin'))[0]
        body.with_name(body.name[:-len('.body.bin')] + '.json').unlink()
        self.refused('metadata is missing')

    def test_a_schema_3_ledger_and_a_missing_ledger_are_not_closed_out(self):
        self.refused('never creates a ledger', state=self.root / 'empty')
        os.replace(self.state / 'frontier.sqlite', self.root / 'moved.sqlite')
        driver.Frontier(self.state / 'frontier.sqlite').close()
        self.refused('schema-2 ledger only')

    def test_a_file_that_cannot_be_moved_refuses_before_any_receipt_and_leaves_the_folder(self):
        import test_collect_committee_activity_crash as crash
        release = crash.hold_without_delete_sharing(self.orphan[0])
        try:
            with mock.patch.object(driver.time, 'sleep'):
                self.refused('Cannot move')
            self.assertFalse((self.state / ('RECEIPT-' + RUN + '.json')).exists())
            self.assertFalse((self.state / 'crash-quarantine').exists() and any((self.state / 'crash-quarantine').rglob('*')))
        finally:
            release()
        self.assertEqual(self.close_out()['quarantined'], 3)

    def test_the_liveness_check_fails_closed(self):
        self.assertTrue(driver._process_alive('x', None), 'an unusable pid cannot be proven gone')
        self.assertTrue(driver._process_alive(None, None))
        self.assertTrue(driver._process_alive(4, None), 'the System process cannot be opened by a normal user or is alive: either way not gone')
        with mock.patch('ctypes.get_last_error', return_value=5):      # OpenProcess failed with ACCESS_DENIED, not "no such process"
            self.assertTrue(driver._process_alive(4_000_000, None))
        with mock.patch('ctypes.get_last_error', return_value=0):
            self.assertTrue(driver._process_alive(4_000_000, None))
        with mock.patch('ctypes.get_last_error', return_value=87):     # ERROR_INVALID_PARAMETER: there is no such pid
            self.assertFalse(driver._process_alive(4_000_000, None))
        launch = json.loads(self.launched.read_text())
        with mock.patch('ctypes.get_last_error', return_value=5):
            self.launched.write_text(json.dumps(launch))
            self.refused('cannot be proven gone')

    def test_a_pid_outside_the_valid_range_is_never_treated_as_gone(self):      # delta review D2: only 0 < pid < 2**32 can be probed; ctypes would wrap -1 to 0xFFFFFFFF and report "no such pid"
        for pid in (0, -1, -4, 2 ** 32, 2 ** 32 + os.getpid(), 2 ** 64 + 1, True):
            self.assertTrue(driver._process_alive(pid, None), pid)
            with mock.patch('ctypes.get_last_error', return_value=87):          # even where OpenProcess would say "no such process", the range check comes first
                self.assertTrue(driver._process_alive(pid, None), pid)
        with mock.patch('ctypes.get_last_error', return_value=87):
            self.assertFalse(driver._process_alive(2 ** 32 - 1, None), 'the largest valid pid is still probed, and an absent process is gone')
        launch = json.loads(self.launched.read_text())
        for pid in (0, -1, 2 ** 32):
            with mock.patch('ctypes.get_last_error', return_value=87):
                self.launched.write_text(json.dumps({**launch, 'child_pid': pid}))
                self.refused('cannot be proven gone')
                self.launched.write_text(json.dumps({**launch, 'preflight': {'observer_pid': pid, 'observer_creation_filetime': 5}}))
                self.refused('cannot be proven gone')

    def test_the_observer_named_in_launched_json_must_be_gone_too(self):
        launch = json.loads(self.launched.read_text())
        self.launched.write_text(json.dumps({**launch, 'preflight': {'observer_pid': os.getpid(), 'observer_creation_filetime': None}}))
        self.refused('observer process')
        self.launched.write_text(json.dumps({**launch, 'preflight': {'observer_pid': 'x'}}))
        self.refused('unusable observer')
        self.launched.write_text(json.dumps({**launch, 'preflight': {'observer_pid': 4_000_001, 'observer_creation_filetime': 5}}))
        self.assertEqual(self.close_out()['quarantined'], 3, 'a dead observer does not block')

    def test_a_launched_json_without_a_child_process_id_cannot_prove_it_is_gone(self):
        launch = json.loads(self.launched.read_text())
        launch.pop('child_pid')
        self.launched.write_text(json.dumps(launch))
        self.refused('names no child process id')

    def test_no_provider_call_is_possible_or_made(self):
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=AssertionError('a close-out never builds a transport')):
            with mock.patch.object(accounted.AccountedTransport, 'fetch', side_effect=AssertionError('no provider call')):
                self.close_out()

    def test_process_probe_reports_a_dead_pid_and_never_signals_a_live_one(self):
        self.assertFalse(driver._process_alive(4_000_000, None), 'no such process: proven gone')
        self.assertTrue(driver._process_alive(os.getpid(), None))
        self.assertFalse(driver._process_alive(os.getpid(), 12345), 'a different creation time means a reused pid, not the launched process')


class CloseOutCommandLine(Legacy):
    def invoke(self, *extra, state=None):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['collect', '--state', str(state or self.state), '--close-out-legacy-run', str(self.folder)] + list(extra)), redirect_stdout(out):
            driver.main()
        return json.loads(out.getvalue())

    def flags(self):
        return ['--baseline-snapshot', str(self.snapshot), '--baseline-receipt', str(self.baseline_receipt), '--launched', str(self.launched)]

    def test_the_subcommand_runs_offline_under_the_lease_and_prints_its_summary(self):
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=AssertionError('offline')):
            printed = self.invoke(*self.flags())
        self.assertEqual((printed['closed_out'], printed['run_id'], printed['http_attempts']), (True, RUN, 4))
        self.assertTrue((self.state / 'frontier.sqlite.run-lease').exists())

    def test_incomplete_or_mixed_flags_are_parser_errors_before_anything_is_touched(self):
        before = tree(self.state)
        cases = ([], ['--baseline-snapshot', str(self.snapshot)], self.flags() + ['--live'], self.flags() + ['--seeds', 'x'], self.flags() + ['--workers', '2'],
                 self.flags() + ['--recover-crashed-runs'], self.flags() + ['--init-new-ledger'])
        for extra in cases:
            with self.subTest(extra=extra), mock.patch('sys.stderr', new=io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.invoke(*extra)
        with self.assertRaises(SystemExit), mock.patch('sys.stderr', new=io.StringIO()):
            self.invoke(*self.flags(), state=self.root)      # the folder must sit directly in --state
        self.assertEqual(tree(self.state), before)

    def test_the_baseline_flags_belong_to_the_close_out_only(self):
        with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.state), '--recover-crashed-runs'] + self.flags()), mock.patch('sys.stderr', new=io.StringIO()):
            with self.assertRaises(SystemExit):
                driver.main()


if __name__ == '__main__':
    unittest.main()
