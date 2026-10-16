"""tools/replay_parity.py (D-180): the mask guard for protected locations, the order- and path-invariant money check that sees a one-cent change, served-count
equality, the judge end to end on synthetic runs, the streak, and one real record -> replay -> replay -> judge round trip through replay_proxy.py with a fake job
and a local server. Synthetic data and fake keys only; Glass Box events are stubbed."""
import functools
import gzip
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import compute_secrets as secrets  # noqa: E402
import replay_parity as rpar  # noqa: E402
import replay_proxy as rp  # noqa: E402

try:
    import cryptography  # noqa: F401
except ImportError:
    cryptography = None
KEYS = ['fake-key-aaaaaaaaaaaa', 'fake-key-bbbbbbbbbbbb']


def write_run(folder, amounts, stamp, where='x'):
    """One synthetic job output: JSON and SQLite, a run stamp (noise) and the money."""
    folder.mkdir(parents=True)
    rows = [{'sub_id': 's%d' % i, 'amount': a, 'role': 'contribution', 'seen': stamp} for i, a in enumerate(amounts)]
    (folder / 'evidence.json').write_text(json.dumps({'retrieved_at': stamp, 'folder': where, 'receipts': rows,
                                                      'finances': [{'committee_id': 'C1', 'receipts_as_reported': sum(amounts)}]}), encoding='utf-8')
    db = sqlite3.connect(folder / 'store.sqlite')
    db.execute('CREATE TABLE transfers(id INTEGER PRIMARY KEY, sub_id TEXT, amount REAL, seen_at TEXT)')
    db.executemany('INSERT INTO transfers VALUES (?,?,?,?)', [(i + 1, 's%d' % i, a, stamp) for i, a in enumerate(amounts)])
    db.commit()
    db.close()


class Temp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for stub in (patch.object(rpar, 'emit'), patch.object(rpar, 'identity', return_value=('commit-1', 'digest-1'))):       # no Glass Box event, no git call
            stub.start()
            self.addCleanup(stub.stop)


class GuardAndMoneyTests(Temp):
    def pair(self, a1, a2):
        write_run(self.base / 'A1', *a1)
        write_run(self.base / 'A2', *a2)
        mask = rpar.parity.variance(self.base / 'A1', self.base / 'A2', rpar.IGNORE)
        return mask, rpar.guard(mask, self.base / 'A1', self.base / 'A2')

    def test_plain_noise_is_maskable(self):
        mask, problems = self.pair(([10.5, 20.0], 't1'), ([10.5, 20.0], 't2'))
        self.assertEqual(problems, [])
        self.assertTrue(mask['files'])                                                   # the stamps are in the mask

    def test_a_reordering_of_the_same_values_is_maskable(self):
        mask, problems = self.pair(([10.5, 20.0], 't1'), ([20.0, 10.5], 't2'))
        self.assertIn('/receipts/0/amount', mask['files']['evidence.json'])             # the amounts moved, so the mask holds them
        self.assertEqual(problems, [])                                                   # but A1 and A2 hold the same values: order only

    def test_a_one_cent_difference_in_a_protected_place_cannot_be_masked(self):
        mask, problems = self.pair(([10.5, 20.0], 't1'), ([10.51, 20.0], 't2'))
        self.assertIn('/receipts/0/amount', mask['files']['evidence.json'])
        self.assertTrue(any('evidence.json: /receipts/*/amount' in p for p in problems), problems)
        self.assertTrue(any('store.sqlite: table:transfers/col:amount' in p for p in problems), problems)

    def test_a_changed_row_count_and_an_opaque_or_gzipped_file_are_refused_too(self):
        mask, problems = self.pair(([10.5, 20.0], 't1'), ([10.5, 20.0, 1.0], 't2'))
        self.assertTrue(any('table:transfers/rows' in p for p in problems), problems)
        for run, rows in (('A1', b'id,Amount\n1,5.00\n'), ('A2', b'id,Amount\n1,5.01\n')):
            (self.base / run / 'warehouse.csv.gz').write_bytes(gzip.compress(rows, mtime=0))
            (self.base / run / 'blob.bin').write_bytes(b'\x00' + run.encode())
        mask = rpar.parity.variance(self.base / 'A1', self.base / 'A2', rpar.IGNORE)
        problems = rpar.guard(mask, self.base / 'A1', self.base / 'A2')
        self.assertTrue(any(p.startswith('warehouse.csv.gz: csv:col:Amount') for p in problems), problems)      # the gzip is read, not trusted as bytes
        self.assertTrue(any(p.startswith('blob.bin: an opaque file') for p in problems), problems)

    def test_the_money_fingerprint_ignores_order_and_location_and_sees_one_cent(self):
        write_run(self.base / 'one' / 'deep' / 'er', [10.5, 20.0, 30.25], 't1', 'C:/a')
        write_run(self.base / 'two', [30.25, 10.5, 20.0], 't9', '/mnt/b')                # shuffled, another stamp, another root
        write_run(self.base / 'three', [30.25, 10.5, 20.01], 't9', '/mnt/b')
        one, two, three = (rpar.fingerprints(self.base / n) for n in ('one/deep/er', 'two', 'three'))
        self.assertEqual(rpar.differences(one, two), [])
        self.assertEqual(one['evidence.json']['/receipts/*/amount']['sum'], '60.75')
        diff = rpar.differences(one, three)
        self.assertTrue(any('/receipts/*/amount' in d for d in diff) and any('table:transfers/col:amount' in d for d in diff), diff)
        self.assertEqual(rpar.differences({'f': {}}, {}), [])

    def test_names_that_say_money_are_protected_and_stamps_are_not(self):
        for leaf in ('amount', 'contribution_receipt_amount', 'receipts_as_reported', 'ending_cash', 'rank', 'percentile', 'cents', 'threshold_x', 'expenditure'):
            self.assertTrue(rpar.money_leaf('/a/0/' + leaf), leaf)
        for leaf in ('retrieved_at', 'seen_at', 'sub_id', 'committee_id', 'role'):
            self.assertFalse(rpar.money_leaf('/a/0/' + leaf), leaf)


class StreakTests(unittest.TestCase):
    def entry(self, name, ok=True, commit='c1', digest='d1', complete=True):
        return {'snapshot': name, 'ok': ok, 'commit': commit, 'digest': digest, 'complete': complete}

    def test_consecutive_passes_on_distinct_snapshots_under_one_identity(self):
        e = self.entry
        self.assertEqual(rpar.streak([]), 0)
        self.assertEqual(rpar.streak([e('s1'), e('s2'), e('s3')]), 3)
        self.assertEqual(rpar.streak([e('s1'), e('s1')]), 1)                              # a re-judged snapshot is still one snapshot
        self.assertEqual(rpar.streak([e('s1'), e('s2'), e('s3', ok=False)]), 0)           # any failure resets
        self.assertEqual(rpar.streak([e('s1'), e('s2', ok=False), e('s3'), e('s4')]), 2)
        self.assertEqual(rpar.streak([e('s1'), e('s2'), e('s3', commit='c2')]), 1)        # a code change resets (the pass under it starts again)
        self.assertEqual(rpar.streak([e('s1'), e('s2'), e('s3', digest='d2'), e('s4', digest='d2')]), 2)
        self.assertEqual(rpar.streak([e('s1'), e('s2'), e('s3', complete=False)]), 2)     # no container run, no claim about the container


class Judging(Temp):
    """The judge on synthetic runs R0, A1, A2 (and B): every gate has a way to fail."""

    def setUp(self):
        super().setUp()
        self.snap = rpar.create('pac-evidence', 'S1', ['--output', '{work}/evidence'], root=self.base / 'root', restrict=False)
        cassette = self.snap.cassette
        (cassette / 'bodies').mkdir(parents=True)
        (cassette / 'cassette.jsonl').write_text('{"v": 1}\n', encoding='utf-8')
        self.meta = self.snap.meta
        self.meta.update(state='recorded', keys_count=2, cassette_sha256=rp.cassette_digest(cassette), http_replay_sha256='h' * 64)
        self.snap.save(self.meta)
        self.served = {'GET a -': 2, 'GET b -': 1}

    def side(self, side, amounts=(10.5, 20.0), stamp=None, served=None, misses=(), where=None, exit_code=0):
        snap = self.snap
        out = snap.runs / side / 'evidence'
        write_run(out, list(amounts), stamp or 'stamp-' + side, where or (str(snap.work) if side != 'B' else '/evidence'))
        files = [{'path': (str(snap.work / 'evidence' / p.name) if side != 'B' else '/evidence/evidence/' + p.name), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                  'bytes': p.stat().st_size} for p in sorted(out.iterdir())]
        receipts = snap.runs / side / '_replay' / 'receipts'
        receipts.mkdir(parents=True)
        run_id = snap.run_id(side)
        (receipts / (run_id + '.json')).write_text(json.dumps({'schema': rpar.cj.SCHEMA, 'job': 'pac-evidence', 'run_id': run_id, 'status': 'ok', 'exit_code': 0,
                                                              'check': {'ok': True, 'problems': []}, 'outputs': files}), encoding='utf-8')
        report = {'mode': 'record' if side == 'R0' else 'replay', 'exit_code': exit_code, 'cassette_sha256': self.meta['cassette_sha256'],
                  'http_replay_sha256': self.meta['http_replay_sha256'], 'served_per_key': dict(served if served is not None else self.served),
                  'misses': list(misses), 'unused': [], 'refused_tls_hosts': []}
        (snap.reports / (side + '.replay.json')).write_text(json.dumps(report), encoding='utf-8')
        (snap.reports / (side + '.run.json')).write_text(json.dumps({'side': side, 'commit': self.meta['commit'], 'digest': self.meta['digest']}), encoding='utf-8')

    def failed(self):
        return sorted(n for n, g in rpar.judge(self.snap)['gates'].items() if not g['ok'])

    def test_clean_runs_pass_and_without_b_the_verdict_is_not_complete(self):
        for side in ('R0', 'A1', 'A2'):
            self.side(side)
        verdict = rpar.judge(self.snap)
        self.assertEqual((verdict['ok'], verdict['complete'], self.failed()), (True, False, []), verdict['gates'])
        self.assertTrue((self.snap.reports / 'judge.json').is_file())
        self.assertTrue((self.snap.reports / 'mask.json').is_file())
        self.side('B')
        self.assertEqual((rpar.judge(self.snap)['ok'], rpar.judge(self.snap)['complete']), (True, True))      # container paths map back to the work folder

    def test_missing_sides_a_miss_and_unequal_served_counts_each_fail_their_gate(self):
        self.side('R0')
        self.side('A1')
        self.assertEqual(self.failed(), ['sides'])
        self.side('A2', served={'GET a -': 2, 'GET b -': 2})
        self.assertEqual(self.failed(), ['served'])
        shutil.rmtree(self.snap.runs / 'A2')
        (self.snap.reports / 'A2.replay.json').unlink()
        self.side('A2', misses=['GET c -'])
        self.assertEqual(self.failed(), ['traffic'])

    def test_a_cent_that_moved_between_the_recording_and_a_replay_fails_the_money_gate(self):
        self.side('R0', amounts=(10.5, 20.0))
        self.side('A1', amounts=(10.5, 20.01))
        self.side('A2', amounts=(10.5, 20.01))
        failed = self.failed()
        self.assertIn('money', failed)                                                    # R0 differs from A1
        self.assertNotIn('mask_guard', failed)                                            # A1 and A2 agree, so nothing is hidden

    def test_a_cent_that_moved_between_two_replays_fails_the_guard_and_the_money_gate(self):
        for side, amounts in (('R0', (10.5, 20.0)), ('A1', (10.5, 20.0)), ('A2', (10.51, 20.0))):
            self.side(side, amounts=amounts)
        self.assertEqual(self.failed(), ['mask_guard', 'money'])

    def test_an_output_changed_after_the_run_fails_the_receipt_gate_and_a_new_cassette_fails_the_recording_gate(self):
        for side in ('R0', 'A1', 'A2'):
            self.side(side)
        (self.snap.runs / 'A1' / 'evidence' / 'evidence.json').write_text('{}', encoding='utf-8')
        self.assertIn('receipts', self.failed())
        (self.snap.cassette / 'cassette.jsonl').write_text('{"v": 2}\n', encoding='utf-8')
        self.assertIn('recording', self.failed())

    def test_the_status_reports_the_streak_from_the_judged_snapshots(self):
        for side in ('R0', 'A1', 'A2', 'B'):
            self.side(side)
        self.assertTrue(rpar.judge(self.snap)['ok'])
        report = rpar.status('pac-evidence', self.base / 'root')
        self.assertEqual((report['streak'], [r['snapshot'] for r in report['snapshots']], report['snapshots'][0]['sides']), (1, ['S1'], ['R0', 'A1', 'A2', 'B']))


class PlanTests(Temp):
    def test_pinned_worker_arguments_scratch_budget_and_container_paths(self):
        snap = rpar.create('collect-fanout', 'F1', ['--source-database', '{work}/source.sqlite', '--industry', 'X', '--run-directory', '{work}/run'], workers=3,
                           root=self.base, restrict=False)
        host = rpar.job_args(snap, str(snap.work))
        self.assertEqual(host[host.index('--source-database') + 1], str(snap.work / 'source.sqlite'))
        budget = Path(host[host.index('--budget-database') + 1])
        self.assertEqual(budget, snap.work / '_replay' / 'budget.sqlite')                    # scratch, never the live .cache
        for flag in ('--initial-workers', '--max-parallel', '--max-lane-ceiling', '--pool-width', '--recovery-workers'):
            self.assertEqual(host[host.index(flag) + 1], '3')
        self.assertEqual((host[host.index('--memory-per-lane-mb') + 1], host[host.index('--concurrency-per-key') + 1]), ('640', '8'))
        box = rpar.job_args(snap, '/collect')
        self.assertEqual((box[box.index('--run-directory') + 1], box[box.index('--budget-database') + 1]), ('/collect/run', '/collect/_replay/budget.sqlite'))
        self.assertNotIn('--budget-database', rpar.job_args(rpar.create('pac-evidence', 'P1', ['--output', '{work}/e'], root=self.base, restrict=False), '/evidence'))

    def test_the_replay_environment_has_dummy_keys_and_scratch_receipts_only(self):
        snap = rpar.create('pac-evidence', 'P1', ['--output', '{work}/e'], root=self.base, restrict=False)
        meta = snap.meta
        meta.update(keys_count=3)
        snap.save(meta)
        keys = rpar.dummy_keys(snap)
        self.assertEqual(keys.read_text().split(), ['replay-dummy-0001', 'replay-dummy-0002', 'replay-dummy-0003'])
        with patch.dict(os.environ, {'FEC_API_KEY': 'real-key-1234567890', 'FEC_API_KEYS': 'real-key-1234567890', 'FEC_API_KEY_2': 'real-key-abcdefghij'}):
            env = rpar.host_env(snap, str(keys))
        self.assertEqual(sorted(k for k in env if k.startswith('FEC_API_KEY')), ['FEC_API_KEYS_FILE'])
        self.assertEqual(Path(env['MENTAT_COMPUTE_RECEIPT_DIR']), snap.work / '_replay' / 'receipts')
        self.assertTrue(Path(env['MENTAT_COMPUTE_RECEIPT_DIR']).is_relative_to(snap.dir))

    def test_path_arguments_outside_the_work_folder_are_refused_in_both_forms(self):
        for flag in ('--source-database', '--run-directory', '--output-receipt', '--output'):
            for args in ([flag, 'C:/real/place'], [flag + '=C:/real/place'], [flag, '/etc/x'], [flag + '=relative/x'], [flag, '{work}/../escape'], [flag + '={work}x/y'], [flag]):
                with self.assertRaises(rpar.Refused, msg=args):
                    rpar.create('pac-evidence', 'R1', ['--committees', 'C1', *args], root=self.base / 'refused', restrict=False)
        self.assertFalse((self.base / 'refused').exists())                                    # refused before anything was made
        for i, args in enumerate((['--output', '{work}/x'], ['--output={work}/x'], ['--output', '{work}'], ['--source-database={work}\\x.sqlite', '--output', '{work}/e'])):
            snap = rpar.create('pac-evidence', 'OK%d' % i, ['--committees', 'C1', *args], root=self.base / 'ok', restrict=False)
            self.assertTrue(snap.dir.is_dir())

    def test_an_input_name_with_a_folder_or_dots_is_refused(self):
        source = self.base / 'source.sqlite'
        source.write_bytes(b'x')
        for name in ('../escape.sqlite', 'sub/x.sqlite', 'sub\\x.sqlite', '..', '.', '', '/abs.sqlite'):
            with self.assertRaises(rpar.Refused, msg=name):
                rpar.create('pac-evidence', 'I1', ['--output', '{work}/e'], inputs=[name + '=' + str(source)], root=self.base / 'bad', restrict=False)
        snap = rpar.create('pac-evidence', 'I2', ['--output', '{work}/e'], inputs=['source.sqlite=' + str(source)], root=self.base / 'bad', restrict=False)
        self.assertEqual(sorted(snap.meta['inputs']), ['source.sqlite'])
        self.assertTrue((snap.inputs / 'source.sqlite').is_file())

    def test_record_refuses_while_a_fanout_runs_and_stages_no_key(self):
        snap = rpar.create('pac-evidence', 'P1', ['--output', '{work}/e'], root=self.base, restrict=False)
        with patch.object(rpar, 'live_fanouts', return_value=[4242]), patch.object(rpar.secrets, 'stage', side_effect=AssertionError('keys staged')):
            with self.assertRaises(rpar.Refused):
                rpar.record(snap)

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL')
    def test_the_snapshot_root_grants_the_current_user_alone(self):
        snap = rpar.create('pac-evidence', 'ACL1', ['--output', '{work}/e'], root=self.base)
        listing = subprocess.run(['icacls', str(snap.dir)], capture_output=True, text=True).stdout
        self.assertIn(os.environ['USERNAME'], listing)
        for broad in ('Everyone', 'BUILTIN\\Users', 'Authenticated Users'):
            self.assertNotIn(broad, listing)

    def test_live_fanouts_sees_a_process_that_names_the_collector(self):
        self.assertEqual(rpar.live_fanouts(), [])
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', 'api_company_cycle_fanout.py'])
        self.addCleanup(child.kill)
        for _ in range(40):
            if child.pid in rpar.live_fanouts():
                break
            threading.Event().wait(0.25)
        self.assertIn(child.pid, rpar.live_fanouts())


FAKE_JOB = r'''
import hashlib, json, os, sys, time, urllib.request
from pathlib import Path
work, url = Path(sys.argv[1]), sys.argv[2]
key = Path(os.environ['FEC_API_KEYS_FILE']).read_text().split()[0]
reply = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={'X-Api-Key': key}), timeout=20).read())
out = work / 'evidence'
out.mkdir(parents=True, exist_ok=True)
(out / 'evidence.json').write_text(json.dumps({'written_at': time.time(), 'work': str(work), 'receipts': reply['results'], 'seen_by_server': reply['seen']}))
receipt = {'schema': 'mentat-compute-job-receipt-v1', 'job': 'pac-evidence', 'run_id': sys.argv[3], 'status': 'ok', 'exit_code': 0, 'check': {'ok': True, 'problems': []},
           'outputs': [{'path': str(out / 'evidence.json'), 'sha256': hashlib.sha256((out / 'evidence.json').read_bytes()).hexdigest(), 'bytes': 1}]}
folder = Path(os.environ['MENTAT_COMPUTE_RECEIPT_DIR'])
folder.mkdir(parents=True, exist_ok=True)
(folder / (sys.argv[3] + '.json')).write_text(json.dumps(receipt))
'''


@unittest.skipIf(cryptography is None, 'cryptography is not installed (record mints on the host)')
class RoundTrip(Temp):
    """record -> replay A1 -> replay A2 -> judge with the real replay_proxy.py around a fake job that reads a local server, and the dirty-cassette refusal."""

    @classmethod
    def setUpClass(cls):
        if not rp.find_http_replay():
            raise unittest.SkipTest('Melange http_replay.py not found; set MELANGE_HTTP_REPLAY')

    def serve(self, leak=None):
        calls = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.headers.get('X-Api-Key'))
                body = json.dumps({'results': [{'amount': 10.5}, {'amount': 20}], 'seen': leak or str(len(calls))}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:%d/v1/schedules/?per_page=100' % server.server_port, calls

    def patched(self, url):
        script = self.base / 'fake_job.py'
        script.write_text(FAKE_JOB, encoding='utf-8')

        def command(snap, side, keys_file):
            http = rp.find_http_replay()
            head = [sys.executable, str(ROOT / 'tools' / 'replay_proxy.py'), 'record' if side == 'R0' else 'replay', str(snap.cassette), '--http-replay', str(http),
                    '--report', str(snap.reports / (side + '.replay.json')), '--no-proxy', '']
            head += ['--secret-file', keys_file] if side == 'R0' else ['--tls', str(snap.tls), '--allow-unused']
            return head + ['--', sys.executable, str(script), str(snap.work), url, snap.run_id(side)]
        stage = functools.partial(secrets.stage, loader=lambda: list(KEYS), base=self.base / 'run', restrict=False)
        wipe = functools.partial(secrets.wipe, base=self.base / 'run')
        every = [*KEYS, 'disabled-key-cccccccccccc']
        import fec_key_store
        for item in (patch.object(rpar, 'host_command', command), patch.object(rpar, 'live_fanouts', return_value=[]), patch.object(rpar.secrets, 'stage', stage),
                     patch.object(rpar.secrets, 'wipe', wipe), patch.object(fec_key_store, 'load_keys', return_value=every)):
            item.start()
            self.addCleanup(item.stop)

    def test_record_replay_replay_judge_and_the_keys_never_reach_the_snapshot(self):
        url, calls = self.serve()
        self.patched(url)
        snap = rpar.create('pac-evidence', 'RT1', ['--output', '{work}/evidence'], root=self.base / 'root', restrict=False)
        self.assertEqual(rpar.record(snap), 0)
        self.assertEqual((snap.meta['state'], snap.meta['keys_count']), ('recorded', 2))
        self.assertEqual(calls, [KEYS[0]])                                                  # R0 was live: the server saw the real key once
        self.assertFalse((self.base / 'run' / 'pac-evidence-RT1-R0').exists())             # staged keys wiped
        self.assertEqual((rpar.replay(snap, 'A1'), rpar.replay(snap, 'A2')), (0, 0))
        self.assertEqual(len(calls), 1)                                                     # the replays never reached the server
        for blob in (p.read_bytes() for p in snap.dir.rglob('*') if p.is_file() and p.parent != snap.keys):
            for key in KEYS:
                self.assertNotIn(key.encode(), blob)
        self.assertEqual(sorted(p.name for p in snap.runs.iterdir()), ['A1', 'A2', 'R0'])
        self.assertFalse(snap.work.exists())                                                # moved to runs/<side>
        verdict = rpar.judge(snap)
        self.assertEqual((verdict['ok'], [n for n, g in verdict['gates'].items() if not g['ok']]), (True, []), verdict['gates'])
        with self.assertRaises(rpar.Refused):
            rpar.replay(snap, 'A1')                                                         # a side runs once

    def test_a_cassette_that_holds_a_disabled_key_is_dirty_and_refused(self):
        url, calls = self.serve(leak='disabled-key-cccccccccccc')                          # the server echoes a key the proxy was not told about
        self.patched(url)
        snap = rpar.create('pac-evidence', 'RT2', ['--output', '{work}/evidence'], root=self.base / 'root', restrict=False)
        self.assertNotEqual(rpar.record(snap), 0)
        self.assertEqual(snap.meta['state'], 'dirty')
        self.assertFalse(snap.cassette.exists())                                            # the cassette that held the key is deleted
        with self.assertRaises(rpar.Refused):
            rpar.replay(snap, 'A1')


if __name__ == '__main__':
    unittest.main()
