"""Inert D075 controls for tools/replay_downstream.py, the offline replay entry point of compute.replay.yaml: the SAME evidence broker inside the job's container, a cassette built from archived
responses in place of the provider, and the job's collector as a child over the real loopback wire. Needs a connect to 127.0.0.1 in this process and in the child, so it runs only under the
loopback guard of work/run_overlay.py (D075_LOOPBACK_OK=1); elsewhere it is skipped with that reason. Test keys only: nothing leaves the machine.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import d075_support as s
import test_collect_committee_activity_concurrency as cc
import archive_cassette
import replay_downstream

LOOPBACK = os.environ.get('D075_LOOPBACK_OK') == '1'
CID = 'ab' * 16


@unittest.skipUnless(LOOPBACK, 'needs the loopback guard of work/run_overlay.py --guard loopback (D075_LOOPBACK_OK=1): a connect to 127.0.0.1 only')
class ReplayEntryPoint(unittest.TestCase):
    """replay_downstream.py: the same broker inside the job's container against a cassette, the job's collector as a child over the real wire."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='D075-replay-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # a run to record: the local transport over the scripted world, archived on disk
        self.local, self.local_transport, _, _ = cc.run_world(self.root, workers=1, adapter=True, world=cc.World(failures=False), seeds=cc.SMALL_SEEDS, name='recorded', ledger=self.root / 'rq.sqlite')
        self.archive = Path(self.local_transport.archive)
        cassette = archive_cassette.Cassette.from_archive([self.archive])
        archive_cassette.write_cassette(cassette, self.root / 'cassette')
        self.entries = len(cassette.entries)
        self.seeds = self.root / 'seeds.json'
        self.seeds.write_text(json.dumps({'schema_version': 1, 'seed_units': cc.SMALL_SEEDS}))
        self.keys = self.root / 'dummy-keys'
        self.keys.write_text('\n'.join('replay-dummy-%04d' % n for n in range(1, 5)) + '\n')
        self.child = self.root / 'child.py'
        self.state, self.hostroot = self.root / 'state', self.root / 'hoststate'

    def write_child(self):
        self.child.write_text('import sys\nsys.path.insert(0, %r)\nimport collect_committee_activity as d\nsys.argv = %r\nd.main()\n' % (str(s.TOOLS), [
            'collect', '--state', str(self.state), '--archive-root', str(self.hostroot), '--transport', 'broker', '--run-id', CID, '--seeds', str(self.seeds),
            '--max-requests', str(self.entries + 50), '--workers', '1', '--live', '--include-later-cycle-context', '--init-new-ledger']), encoding='utf-8')

    def replay(self):
        self.write_child()
        job_args = ['--batch', '1', '--collector-run-id', CID, '--expect-base-sha256', 'b' * 64, '--seeds', '/inputs/seeds.json', '--max-requests', str(self.entries + 50),
                    '--max-failed-attempts', '8', '--workers', '1', '--live']
        report = self.root / 'replay-report.json'
        with mock.patch.dict(os.environ, {'REPLAY_REPORT': str(report), 'MENTAT_API_BUDGET_DB': str(self.root / 'unused.sqlite')}):
            code = replay_downstream.main(['--cassette', str(self.root / 'cassette'), '--dummy-keys', str(self.keys), '--archive-root', str(self.hostroot), '--workdir', str(self.root / 'work'), '--',
                                           sys.executable, str(self.child), '--'] + job_args)
        return code, json.loads(report.read_text())

    def test_the_child_collects_through_the_in_container_broker_and_the_ledger_equals_the_recorded_one(self):
        code, report = self.replay()
        self.assertEqual(code, 0)
        self.assertEqual((report['entries'], report['served'], report['misses'], report['unused'], report['child_exit_code']), (self.entries, self.entries, [], 0, 0))
        self.assertEqual(cc.canonical(self.state / 'frontier.sqlite'), cc.canonical(self.local.path))
        self.assertTrue(Path(report['broker_receipt']).is_file())
        self.assertFalse((self.root / 'work' / 'request-pool.json').exists(), 'the in-container descriptor is removed at the end')
        written = sorted(p.name for p in (self.hostroot / ('responses-' + CID)).iterdir())
        self.assertEqual(written, sorted(p.name for p in self.archive.iterdir()), 'the broker re-archived exactly the recorded files')

    def test_a_request_the_cassette_lacks_is_a_miss_and_the_replay_exits_3(self):
        index = json.loads((self.root / 'cassette' / 'index.json').read_text())
        index['entries'].pop(len(index['entries']) // 2)
        (self.root / 'cassette' / 'index.json').write_text(json.dumps(index))
        code, report = self.replay()
        self.assertEqual(code, 3)
        self.assertEqual(len(report['misses']), 1)
        self.assertEqual(report['child_exit_code'], 0, 'the collector turned the lost answer into its own BrokerUnavailable stop')
        receipt = json.loads(next(self.state.glob('RECEIPT-*.json')).read_text())
        self.assertEqual(receipt['stop']['error_type'], 'BrokerUnavailable')

    def test_only_dummy_keys_may_stand_in_for_the_provider(self):
        self.keys.write_text('real-looking-key-0001\n')
        with self.assertRaises(SystemExit):
            self.replay()

    def test_the_job_arguments_are_what_follows_the_last_double_dash(self):
        self.assertEqual(replay_downstream.job_arguments(['python', 'compute_job.py', 'run', 'x', '--run-id', 'r', '--', '--batch', '1', '--live']), ['--batch', '1', '--live'])
        self.assertEqual(replay_downstream.job_arguments(['a', '--', 'b', '--', 'c']), ['c'])
        with self.assertRaises(SystemExit):
            replay_downstream.job_arguments(['no', 'separator'])


if __name__ == '__main__':
    unittest.main()
