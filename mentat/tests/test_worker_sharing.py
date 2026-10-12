"""Disposable scheduling checks: no network, production keys or live databases."""
from __future__ import annotations

import concurrent.futures
import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import api_company_cycle_fanout as f
import test_merge_company_shards as fixture


class WorkerSharingTest(unittest.TestCase):
    def queue(self, root, resume=False):
        plans = f.schedule_companies([('A', 10), ('B', 1), ('C', 1), ('D', 1), ('E', 1)], (2024, 2026), 2)
        path = root / 'manifest.json'
        f.atomic_json(path, {'lanes': copy.deepcopy(plans)})
        return f.CompanyWorkQueue(plans, {p['lane']:p['tasks'] for p in plans}, path, resume=resume)

    def test_idle_worker_borrows_whole_unstarted_company_and_persists_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            q = self.queue(Path(directory))
            self.assertEqual(q.claim(1)[0][0]['company'], 'A')
            self.assertEqual(q.claim(2)[0][0]['company'], 'B')
            borrowed, resuming = q.claim(1)
            self.assertEqual({t['company'] for t in borrowed}, {'E'})
            self.assertEqual({t['cycle'] for t in borrowed}, {2024, 2026})
            self.assertFalse(resuming)
            saved = f.read_json(q.manifest_path)
            self.assertIn('E', saved['lanes'][0]['claimed_companies'])
            self.assertNotIn('E', saved['lanes'][1]['companies'])
            self.assertEqual(sum(len(p['tasks']) for p in q.plans), 10)
            self.assertEqual(len({t['task_id'] for p in q.plans for t in p['tasks']}), 10)

    def test_claim_survives_crash_before_receipt_and_legacy_owners_are_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            q = self.queue(root)
            q.claim(1); q.claim(2); q.claim(1)
            restarted = f.CompanyWorkQueue(q.plans, {1:[], 2:[t for t in q.plans[1]['tasks'] if t['company']=='B']}, q.manifest_path, resume=True)
            self.assertIsNone(restarted.claim(1))
            self.assertTrue(restarted.claim(2)[1])
            legacy = self.queue(root, resume=True)
            legacy.claim(1)
            self.assertIsNone(legacy.claim(1))
            self.assertFalse(legacy.managed_initialization)
            migrated = f.CompanyWorkQueue(legacy.plans, {p['lane']:p['tasks'] for p in legacy.plans}, legacy.manifest_path, resume=True)
            self.assertFalse(migrated.managed_initialization)

    def test_failed_manifest_write_does_not_dispatch_or_lose_work(self):
        with tempfile.TemporaryDirectory() as directory:
            q = self.queue(Path(directory))
            before = copy.deepcopy(q.plans)
            with mock.patch.object(f, 'atomic_json', side_effect=PermissionError('injected')):
                with self.assertRaises(PermissionError): q.claim(1)
            self.assertEqual(q.plans, before)
            self.assertEqual(q.pending_companies(), 5)
            self.assertEqual(q.claimed, set())

    def test_resource_leases_serialize_shared_database_or_key_and_release_on_error(self):
        for same_database in (True, False):
            gate = f.LaneLimiter(3)
            active = set()
            lock = threading.Lock()
            calls = []
            def execute(run, **kwargs):
                resources = {str(run.database), run.key}
                with lock:
                    self.assertFalse(active & resources)
                    active.update(resources)
                time.sleep(.015)
                with lock: active.difference_update(resources); calls.append(run.lane)
                if run.lane == 0: raise RuntimeError('injected')
                return {}
            runs = [f.LaneRun(i, Path('shared.db' if same_database else f'{i}.db'), Path('a'), Path('r'), Path('s'), Path('l'), f'key-{i}' if same_database else 'shared-key', '', (), 1) for i in range(3)]
            with mock.patch.object(f, 'execute_lane', side_effect=execute), concurrent.futures.ThreadPoolExecutor(3) as pool:
                futures=[pool.submit(f.execute_lane_with_limit,gate,run) for run in runs]
                for i,future in enumerate(futures):
                    if i==0:
                        with self.assertRaisesRegex(RuntimeError,'injected'): future.result(timeout=3)
                    else: future.result(timeout=3)
            self.assertEqual(sorted(calls), [0,1,2])
            self.assertEqual(gate.active(),0)
            self.assertEqual(gate.resources,set())

    def test_stop_wakes_waiters_without_starting_their_work(self):
        gate=f.LaneLimiter(2)
        self.assertTrue(gate.acquire(('busy',)))
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future=pool.submit(gate.acquire,('busy',))
            gate.stop()
            self.assertFalse(future.result(timeout=2))
        gate.release(('busy',))
        self.assertFalse(gate.acquire())

    def test_second_supervisor_is_fenced_and_crash_releases_run_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / 'ready'
            script = '\n'.join([
                'import pathlib, sys, time',
                'sys.path.insert(0, sys.argv[1])',
                'from api_company_cycle_fanout import supervisor_lock',
                'path = pathlib.Path(sys.argv[2])',
                'ready = pathlib.Path(sys.argv[3])',
                'with supervisor_lock(path):',
                '    ready.write_text("ready")',
                '    time.sleep(30)',
            ])
            # A real second process must hold the operating-system SQLite lock.
            # Keep its script in a file so Python's compound with statement parses.
            helper = root / 'hold.py'
            helper.write_text(script, encoding='utf-8')
            process = subprocess.Popen(
                [sys.executable, str(helper), str(Path(f.__file__).parent), str(root), str(ready)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists(), process.stderr.read() if process.poll() is not None else '')
                first = f.read_json(root / 'supervisor-owner.json')
                self.assertTrue(first['active'])
                with self.assertRaisesRegex(RuntimeError, 'Another fanout supervisor'):
                    with f.supervisor_lock(root):
                        self.fail('duplicate supervisor entered the run')
                with mock.patch.object(f, '_main_unlocked') as dispatched:
                    with self.assertRaisesRegex(RuntimeError, 'Another fanout supervisor'):
                        f.main(['--source-database', str(root/'unused.db'),
                                '--industry', 'Fintech', '--run-directory', str(root)])
                    dispatched.assert_not_called()
            finally:
                process.kill()
                process.communicate(timeout=5)
            # The old receipt is still marked active after a hard crash. Its
            # token cannot fence a new owner once the OS has freed the lock.
            self.assertTrue(f.read_json(root / 'supervisor-owner.json')['active'])
            with f.supervisor_lock(root) as replacement:
                self.assertNotEqual(replacement, first['token'])
                self.assertEqual(f.read_json(root / 'supervisor-owner.json')['token'], replacement)
            self.assertFalse(f.read_json(root / 'supervisor-owner.json')['active'])

    def test_child_writer_conflict_does_not_replace_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assignment = root / 'assignments.json'
            receipt = root / 'receipt.json'
            task = {'task_id': 'fixture-2026', 'company': 'Fixture', 'cycle': 2026}
            f.atomic_json(assignment, {'industry': 'Fintech', 'tasks': [task]})
            f.atomic_json(receipt, {'status': 'succeeded', 'checkpoint': 'preserved'})
            before = receipt.read_bytes()
            run = f.LaneRun(1, root/'lane.db', assignment, receipt,
                            root/'status.json', root/'process.log', 'fixture-key',
                            f.key_id('fixture-key'), (task,), 1)
            with mock.patch.object(f.subprocess, 'run', return_value=mock.Mock(returncode=4)):
                with self.assertRaisesRegex(RuntimeError, 'Another R lane writer'):
                    f.execute_lane(run, rscript_path=root/'Rscript.exe',
                        lane_script=root/'tools'/'collect_api_lane.R',
                        r_library_path=None, concurrency=1, sqlite_cache_mb=32,
                        budget_database=root/'budget.db')
            self.assertEqual(receipt.read_bytes(), before)

    def test_nonzero_lane_exit_cannot_certify_successful_task_receipt(self):
        with tempfile.TemporaryDirectory(prefix="mentat-lane-exit-") as directory:
            root = Path(directory)
            task = {'task_id': 'fixture-2026', 'company': 'Fixture', 'cycle': 2026}
            assignment = root / 'assignments.json'
            receipt = root / 'receipt.json'
            f.atomic_json(assignment, {'industry': 'Fintech', 'tasks': [task]})
            child = root / 'fake-collector.py'
            child.write_text(
                "import json,sys\n"
                "from pathlib import Path\n"
                "Path(sys.argv[4]).write_text(json.dumps({'status':'succeeded',"
                "'quick_check':'corrupt','tasks':[{'task_id':'fixture-2026',"
                "'company':'Fixture','cycle':2026,'status':'succeeded'}]}))\n"
                "raise SystemExit(3)\n",
                encoding='utf-8',
            )
            run = f.LaneRun(1, root/'lane.db', assignment, receipt,
                            root/'status.json', root/'process.log', 'fixture-key',
                            f.key_id('fixture-key'), (task,), 1)
            result = f.execute_lane(
                run, rscript_path=Path(sys.executable), lane_script=child,
                r_library_path=None, concurrency=1, sqlite_cache_mb=32,
                budget_database=root/'budget.db',
            )
            self.assertEqual(result['exit_code'], 3)
            self.assertEqual(result['receipt']['status'], 'failed')
            self.assertEqual(result['receipt']['tasks'][0]['status'], 'failed')
            self.assertIn('code 3', result['receipt']['tasks'][0]['error'])

    def test_resume_before_any_receipt_uses_new_attempt_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.db'
            fixture.create_database(source)
            with closing(sqlite3.connect(source)) as db, db:
                db.execute('CREATE TABLE companies(industry TEXT, company TEXT, variant TEXT)')
                db.execute("INSERT INTO companies VALUES ('Fintech', 'Fixture', 'FIXTURE')")
            run = root / 'run'
            arguments = ['--source-database', str(source), '--industry', 'Fintech',
                         '--cycles', '2024', '--run-directory', str(run),
                         '--rscript-path', sys.executable,
                         '--budget-database', str(root/'budget.db'),
                         '--max-parallel', '1', '--no-shared-requests']
            lane = run / 'lanes' / 'lane-01'
            with mock.patch.object(f, 'discover_keys', return_value=['fixture-key']), \
                 mock.patch.object(f, 'execute_lane', side_effect=RuntimeError('injected crash')):
                with self.assertRaisesRegex(RuntimeError, 'injected crash'):
                    f.main(arguments)
                original = list(lane.glob('assignments.work-1-*.json'))
                self.assertEqual(len(original), 1)
                first_bytes = original[0].read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'injected crash'):
                    f.main([*arguments, '--resume'])
            attempts = list(lane.glob('assignments.work-1-*.json'))
            self.assertEqual(len(attempts), 2)
            self.assertEqual(original[0].read_bytes(), first_bytes)
            self.assertNotEqual(attempts[0].name, attempts[1].name)
            manifest = f.read_json(run/'manifest.json')
            self.assertIn('process.resume-1-', manifest['lanes'][0]['log'])

    def test_borrowed_work_merges_every_cycle_once_and_resume_skips_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source.db'; fixture.create_database(source)
            with closing(sqlite3.connect(source)) as con, con:
                con.execute('CREATE TABLE companies(industry TEXT, company TEXT, variant TEXT)')
                con.executemany("INSERT INTO companies VALUES ('Fintech', ?, ?)",
                    [('A',f'A{i}') for i in range(10)]+[(c,c) for c in 'BCDE'])
            borrowed=threading.Event(); executions=[]; fail_b=True
            def execute(run, **kwargs):
                company=run.tasks[0]['company']
                if company=='B' and run.attempt==1:
                    self.assertTrue(borrowed.wait(3), 'idle worker did not borrow pending work')
                if run.lane==1 and company!='A': borrowed.set()
                succeeded=not(fail_b and company=='B')
                assignment=f.read_json(run.assignments)
                for marker in assignment.get('initialization_markers',{}).values():
                    f.atomic_json(Path(marker), {'initialized':True})
                tasks=[{**t,'status':'succeeded' if succeeded else 'failed','wall_clock_seconds':.01} for t in run.tasks]
                if succeeded:
                    with closing(sqlite3.connect(run.database)) as con, con:
                        con.executemany('INSERT OR REPLACE INTO contributions(sub_id,company,amount) VALUES(?,?,?)',
                            [(t['task_id'],t['company'],t['cycle']) for t in tasks])
                receipt={'status':'succeeded' if succeeded else 'failed',
                         'quick_check':'ok','tasks':tasks}
                f.atomic_json(run.receipt,receipt)
                executions.extend((t['company'],t['cycle']) for t in tasks)
                return {'lane':run.lane,'attempt':run.attempt,'receipt':receipt,'exit_code':0 if succeeded else 2}
            args=['--source-database',str(source),'--industry','Fintech','--cycles','2024|2026',
                '--run-directory',str(root/'run'),'--rscript-path',sys.executable,'--budget-database',str(root/'budget.db'),
                '--max-parallel','2','--initial-workers','2','--recovery-workers','2']
            before=source.read_bytes()
            with mock.patch.object(f,'discover_keys',return_value=['key-a','key-b']),mock.patch.object(f,'execute_lane',side_effect=execute):
                self.assertEqual(f.main(args),2)
                self.assertEqual(source.read_bytes(),before)
                manifest=f.read_json(root/'run/manifest.json')
                self.assertGreater(len(manifest['scheduling']['handoffs']),0)
                fail_b=False; executions.clear()
                self.assertEqual(f.main([*args,'--resume']),0)
                self.assertEqual(sorted(executions),[('B',2024),('B',2026)])
                # Simulate a lost terminal receipt after the source commit.
                # Ownership handoffs in the manifest must still bind to the
                # committed intent, with no extra lane dispatch or ingestion.
                (root/'run/receipt.json').unlink()
                executions.clear()
                self.assertEqual(f.main([*args,'--resume']),0)
                self.assertEqual(executions, [])
                self.assertTrue(f.read_json(root/'run/receipt.json')['merge']['recovered_after_commit'])
            with closing(sqlite3.connect(source)) as con:
                rows=con.execute('SELECT company,amount FROM contributions ORDER BY company,amount').fetchall()
            self.assertEqual(rows,[(c,y) for c in 'ABCDE' for y in (2024,2026)])


if __name__=='__main__': unittest.main()
