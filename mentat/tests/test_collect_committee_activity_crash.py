"""Inert D071 crash controls: TerminateProcess hard kills at four points (N=4 and N=1), Ctrl-C in the writer, and a Windows
sharing violation during recovery. A killed collector must leave a state that --recover-crashed-runs reconciles into a
receipt the audit validator accepts unchanged, and a resumed run must reach the ledger an uninterrupted run reaches.

Only the kill itself needs a child process (the child monkeypatches; the collector carries no test hooks). Recovery, resume and
validation run in this process. No credentials, provider, network or canonical state: see test_collect_committee_activity_concurrency.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import test_collect_committee_activity_concurrency as base
from test_collect_committee_activity_concurrency import KEYS, SEEDS, World, ScriptedOpener, accounted, driver

CHILD = 'import test_collect_committee_activity_crash as t; t.child_main()'


# ---------------------------------------------------------------- the child: a real collector main() that blocks at one point
def child_main():
    spec = json.loads(sys.argv[1])
    state, marker, workers, point, m = Path(spec['state']), Path(spec['marker']), spec['workers'], spec['point'], spec['m']
    lock, blocked, done = threading.Lock(), [0], [0]
    need = workers if point in ('http', 'body') else 1   # every worker blocks in HTTP/archive; only the writer blocks at a commit
    before = set(state.glob('responses-*'))

    def block():
        with lock:
            blocked[0] += 1
            if blocked[0] == need:
                marker.write_text(point)
        time.sleep(3600)

    def folder():
        return next(iter(set(state.glob('responses-*')) - before))

    def settle():
        # every in-flight worker finishes its archive pair; the writer is held, so nothing else can change
        last, since = -1, time.monotonic()
        while time.monotonic() - since < 1.0:
            now = len(list(folder().glob('*.json')))
            if now != last:
                last, since = now, time.monotonic()
            time.sleep(0.02)

    def hook(number, key, query):
        if point == 'http' and number > m:
            block()
    opener = ScriptedOpener(World(failures='pairs'), hook=hook)
    if point == 'body':
        real = accounted.durable_new

        def durable(path, body):
            if str(path).endswith('.json'):
                with lock:
                    done[0] += 1
                    late = done[0] > m
                if late:
                    block()
            return real(path, body)
        accounted.durable_new = durable
    if point in ('pair', 'txn'):
        name = '_commit' if point == 'pair' else '_apply'
        original = getattr(driver.Frontier, name)

        def gate(self, *args, **kwargs):
            with lock:
                done[0] += 1
                late = done[0] > m
            if late and point == 'pair':   # before the commit transaction starts
                settle()
                block()
            result = original(self, *args, **kwargs)   # for 'txn' the page's inserts are now in the open write transaction
            if late:
                settle()
                block()
            return result
        setattr(driver.Frontier, name, gate)

    def live(archive, requested, **options):
        return accounted.AccountedTransport(list(KEYS), spec['quota'], archive, requested, opener=opener, cap=100000)
    sys.argv = ['collect', '--state', str(state), '--seeds', spec['seeds'], '--live', '--include-later-cycle-context',
                '--workers', str(workers), '--max-requests', '5000']
    with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=live):
        driver.main()


# ---------------------------------------------------------------- Windows: hold a file without delete sharing
def hold_without_delete_sharing(path):
    kernel = ctypes.windll.kernel32
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)   # GENERIC_READ, FILE_SHARE_READ only
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError('could not hold ' + str(path))
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    return lambda: kernel.CloseHandle(handle)


PAIRS = World(failures='pairs')
REFERENCE = {}   # the uninterrupted N=1 ledger of the same world, computed once


class CrashBase(base.Base):
    def adapter(self, **options):
        return super().adapter(world=PAIRS, **options)

    def first_batch(self):
        """Accepted batch A (20 attempts, serial) so every chain has a prior receipt and snapshot."""
        self.seeds_rows = SEEDS
        receipt, data = self.collect(['--workers', '1', '--max-requests', '20'], self.adapter())
        self.assertEqual(data['http_attempts'], 20)
        return receipt, self.snapshot(receipt)

    def refuses_to_start(self):
        before = (self.state / 'frontier.sqlite').read_bytes()
        journal = (self.state / 'frontier.sqlite-journal').exists()
        with self.assertRaises(SystemExit) as caught:
            self.invoke(['--seeds', str(self.seeds), '--max-requests', '50', '--live'], live=lambda *a, **k: self.fail('a refused start must not reach the transport'))
        self.assertIn('Unresolved collector run', str(caught.exception))
        self.assertEqual((self.state / 'frontier.sqlite').read_bytes(), before)
        self.assertEqual((self.state / 'frontier.sqlite-journal').exists(), journal, 'a refused start does not touch the crashed ledger')

    def recover_resume_and_validate(self, receipt_a, proof_a, *, abandoned, replayed, quarantined, unrecorded, committed):
        """Everything that must hold after any crash: refusal, recovery receipt, validator chain, idempotence, resume, final ledger."""
        run = driver.unresolved_runs(self.state)[0][0]
        self.refuses_to_start()
        printed = self.invoke(['--recover-crashed-runs'])
        self.assertTrue(printed['recovered'])
        self.assertFalse((self.state / 'frontier.sqlite-journal').exists(), 'recovery rolled the hot journal back')
        quarantine = self.state / 'crash-quarantine' / run
        if abandoned:
            self.assertIsNone(printed['receipt'])
            self.assertTrue((self.state / ('RUN-' + run + '.ABANDONED.json')).exists())
            self.assertFalse((self.state / ('RECEIPT-' + run + '.json')).exists())
            self.assertEqual(printed['quarantined'], quarantined)
            self.assert_sound([receipt_a], label='abandoned')
            chain, proofs = [receipt_a], []
        else:
            receipt_b = Path(printed['receipt'])
            data = json.loads(receipt_b.read_bytes())
            self.assertTrue(data['recovered_after_crash'])
            self.assertEqual(data['stop']['error_type'], 'CrashRecovered')
            self.assertEqual((data['recovery']['replayed_pairs'], len(data['recovery']['quarantined'])), (replayed, quarantined))
            self.assertEqual(data['http_attempts'], committed + replayed)
            self.assertIn('unrecorded_physical_attempts', data['recovery'])
            for entry in data['recovery']['quarantined']:
                moved = quarantine / entry['name']
                self.assertEqual((hashlib.sha256(moved.read_bytes()).hexdigest(), moved.stat().st_size), (entry['sha256'], entry['bytes']))
                self.assertFalse((self.state / ('responses-' + run) / entry['name']).exists())
            self.assert_sound([receipt_a, receipt_b], [proof_a], label='recovered')
            chain, proofs = [receipt_a, receipt_b], [proof_a, self.snapshot(receipt_b)]
        again = self.invoke(['--recover-crashed-runs'])
        self.assertFalse(again['recovered'])
        receipt_c, data_c = self.collect(['--workers', '4', '--max-requests', '5000'], self.adapter())
        self.assertIsNone(data_c['stop'])
        chain.append(receipt_c)
        self.assert_sound(chain, proofs if not abandoned else [proof_a], label='resumed')
        # The resumed ledger is the uninterrupted one: same pages, observations, edges, origins, endpoint outcomes.
        if 'tables' not in REFERENCE:
            REFERENCE['tables'] = base.canonical(base.run_world(self.root, name='reference', world=PAIRS, ledger=self.root / 'reference-quota.sqlite')[0].path)
        got, want = base.canonical(self.state / 'frontier.sqlite'), REFERENCE['tables']
        for table in base.TABLES:
            if table != 'failures':
                self.assertEqual(got[table], want[table], table)
        recorded = self.rows('SELECT COALESCE(SUM(attempt_count),0) FROM endpoints')[0][0]
        self.assertEqual(self.budget_used() - recorded, unrecorded, 'only the requests in flight at the kill are charged but never recorded')

    def spawn(self, workers, point, m):
        marker = self.root / 'marker.txt'
        spec = {'state': str(self.state), 'seeds': str(self.seeds), 'quota': str(self.root / 'quota.sqlite'), 'workers': workers, 'point': point, 'm': m, 'marker': str(marker)}
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', LOCALAPPDATA=str(self.root / 'localappdata'),
                           MENTAT_API_BUDGET_DB=str(self.root / 'unused-budget.sqlite'))
        for name in [n for n in environment if n.upper().startswith('FEC_API_KEY')]:
            environment.pop(name)
        log = (self.root / 'child.log').open('wb')
        process = subprocess.Popen([sys.executable, '-B', '-c', CHILD, json.dumps(spec)], cwd=Path(__file__).resolve().parent, env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 120
            while not marker.exists():
                if process.poll() is not None:
                    self.fail('child ended early (%s): %s' % (process.returncode, (self.root / 'child.log').read_text(errors='replace')[-2000:]))
                if time.monotonic() > deadline:
                    self.fail('child never reached the kill point')
                time.sleep(0.05)
            process.kill()   # TerminateProcess on Windows: no cleanup of any kind runs
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=30)
            log.close()
        self.assertNotEqual(process.returncode, 0)


class HardKill(CrashBase):
    CASES = (('http', 5, False), ('body', 0, True), ('pair', 5, False), ('txn', 5, False))

    def run_cases(self, workers, cases):
        for point, m, abandoned in cases:
            with self.subTest(workers=workers, point=point):
                self.fresh()
                receipt_a, proof_a = self.first_batch()
                self.spawn(workers, point, m)
                self.assertEqual(list(self.state.glob('RECEIPT-*.json')), [receipt_a], 'the killed run wrote no receipt')
                if point == 'txn':
                    self.assertTrue((self.state / 'frontier.sqlite-journal').exists(), 'killed inside the commit transaction: hot journal left behind')
                # At a commit gate the writer may hold fewer than N dispatched jobs (it commits every ready result before it refills),
                # so the orphans are exactly the archived pairs beyond the m already committed - never fewer than one.
                pairs = len(list(max(self.state.glob('responses-*'), key=lambda p: p.stat().st_mtime_ns).glob('*.body.bin')))
                if point in ('pair', 'txn'):
                    self.assertTrue(1 <= pairs - m <= workers, pairs - m)
                self.recover_resume_and_validate(
                    receipt_a, proof_a, abandoned=abandoned, committed=m,
                    replayed=pairs - m if point in ('pair', 'txn') else 0,
                    quarantined=workers if point == 'body' else 0,
                    unrecorded=workers if point in ('http', 'body') else 0)

    def test_hard_kill_at_four_points_with_four_workers_recovers_and_the_validator_accepts_it(self):
        self.run_cases(4, self.CASES)

    def test_hard_kill_at_four_points_with_one_worker_recovers_and_the_validator_accepts_it(self):
        self.run_cases(1, (('http', 5, False), ('body', 4, False), ('pair', 5, False), ('txn', 5, False)))


class CtrlC(CrashBase):
    def test_keyboard_interrupt_in_the_writer_lets_workers_finish_commits_nothing_and_recovers(self):
        receipt_a, proof_a = self.first_batch()
        real, calls = driver.wait, []

        def interrupt(*args, **kwargs):
            calls.append(1)
            if len(calls) == 4:
                raise KeyboardInterrupt
            return real(*args, **kwargs)
        with mock.patch.object(driver, 'wait', interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.collect(['--workers', '4', '--max-requests', '5000'], self.adapter(opener=ScriptedOpener(PAIRS, delay=0.03)))
        self.assertEqual(list(self.state.glob('RECEIPT-*.json')), [receipt_a])
        self.assertEqual(len(list(self.state.glob('RUN-*.START.json'))), 2)
        crashed = max(self.state.glob('responses-*'), key=lambda p: p.stat().st_mtime_ns)
        names = {p.name for p in crashed.iterdir()}
        self.assertEqual({n[:-9] for n in names if n.endswith('.body.bin')}, {n[:-5] for n in names if n.endswith('.json')}, 'every worker finished its archive pair')
        orphans = len(names) // 2 - self.rows("SELECT COUNT(*) FROM pages WHERE archive_path LIKE ?", '%' + crashed.name + '%')[0][0]
        self.assertGreaterEqual(orphans, 1)
        committed = self.rows("SELECT COUNT(*) FROM pages WHERE archive_path LIKE ?", '%' + crashed.name + '%')[0][0] + \
            self.rows("SELECT COUNT(*) FROM failures WHERE archive_record_json LIKE ?", '%' + crashed.name + '%')[0][0]
        self.recover_resume_and_validate(receipt_a, proof_a, abandoned=False, committed=committed, replayed=orphans, quarantined=0, unrecorded=0)


class SharingViolation(CrashBase):
    def test_recovery_retries_a_windows_sharing_violation_then_refuses_with_nothing_changed(self):
        real = accounted.durable_new

        def crash(path, body):
            if str(path).endswith('.json') and 'attempt-000004-' in str(path):
                raise KeyboardInterrupt   # the process ends between the body fsync and the metadata
            return real(path, body)
        self.seeds_rows = SEEDS
        with mock.patch.object(accounted, 'durable_new', crash):
            with self.assertRaises(KeyboardInterrupt):
                self.collect(['--workers', '1', '--max-requests', '5000'], self.adapter())
        folder = next(self.state.glob('responses-*'))
        unpaired = next(p for p in folder.iterdir() if p.name.startswith('attempt-000004-') and p.name.endswith('.body.bin'))
        before = (base.tree_listing(self.state), (self.state / 'frontier.sqlite').read_bytes())
        release = hold_without_delete_sharing(unpaired)
        started = time.monotonic()
        try:
            with self.assertRaises(SystemExit) as caught:
                self.invoke(['--recover-crashed-runs'])
        finally:
            release()
        self.assertGreater(time.monotonic() - started, 1.0, 'the move was retried before the refusal')
        self.assertIn('Cannot move', str(caught.exception))
        self.assertEqual((base.tree_listing(self.state), (self.state / 'frontier.sqlite').read_bytes()), before, 'a refused recovery changes nothing')
        self.assertFalse((self.state / 'crash-quarantine').exists() and any((self.state / 'crash-quarantine').rglob('*.bin')))
        printed = self.invoke(['--recover-crashed-runs'])
        receipt = Path(printed['receipt'])
        data = json.loads(receipt.read_bytes())
        self.assertEqual((data['recovery']['replayed_pairs'], [e['name'] for e in data['recovery']['quarantined']]), (0, [unpaired.name]))
        self.assertEqual(data['new_pages_this_invocation'], 3)
        self.assert_sound([receipt])


if __name__ == '__main__':
    unittest.main()
