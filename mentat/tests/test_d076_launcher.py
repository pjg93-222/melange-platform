"""Inert D076 controls (review item M2) for forwarding the bulk activity index through the two launchers: the container job (tools/compute_job.py) and the host-native wrapper
(tools/run_job.ps1). The D075 storage rig is reused unchanged (temporary stand-ins for /frontier, /hoststate, /export, /inputs; a STUB collector as the child); the stub here also writes the
`bulk_activity_index` block into its receipt the way the real collector does. No Docker, credential, provider or network.

The wrapper is a PowerShell script: its allow-list is checked as text (the refusal path itself is exercised by test_d075_launcher), and the PlanOnly output is run when PowerShell exists.
"""
import json
import os
from pathlib import Path
import re
import unittest
from unittest import mock

import test_d075_storage as ds
import test_d075_launcher as dl

cj, ROOT, CID, RUN = ds.cj, ds.ROOT, ds.CID, ds.RUN
PIN = 'a1' * 32
STUB = ds.STUB.replace("(state / ('RECEIPT-' + cid + '.json')).write_text(json.dumps(receipt), encoding='utf-8')",
                       "if '--bulk-activity-index' in argv and mode != 'no-index-block':\n"
                       "    receipt['bulk_activity_index'] = {'sha256': get('--expect-bulk-index-sha256') if mode != 'wrong-index' else '0' * 64}\n"
                       "if '--bulk-activity-index' not in argv and mode == 'stray-index-block':\n"
                       "    receipt['bulk_activity_index'] = {'sha256': 'f' * 64}\n"
                       "(state / ('RECEIPT-' + cid + '.json')).write_text(json.dumps(receipt), encoding='utf-8')")
assert STUB != ds.STUB


class Rig(ds.Rig):
    def setUp(self):
        super().setUp()
        self.stub.write_text(STUB, encoding='utf-8')
        self.index = self.inputs / 'bulk-activity-index.json'
        self.index.write_text('{}')

    def indexed(self, **replace):
        flags = {'--bulk-activity-index': self.index.as_posix(), '--expect-bulk-index-sha256': PIN}
        flags.update(replace)
        return self.args() + [item for flag, value in flags.items() if value is not None for item in (flag, value)]


class Arguments(Rig):
    def test_both_flags_reach_the_collector_after_the_priority_file_and_before_the_ccl_flags(self):
        extra = self.indexed() + ['--priority-candidates', (self.inputs / 'priority.json').as_posix(), '--ccl', '2024=/mentat/data/ccl.txt']
        ctx = cj._context('collect-downstream', RUN, self.env)
        argv = cj.JOBS['collect-downstream'].argv(ctx, extra)
        at = argv.index('--bulk-activity-index')
        self.assertEqual(argv[at - 2:at], ['--priority-candidates', (self.inputs / 'priority.json').as_posix()])
        self.assertEqual(argv[at:at + 4], ['--bulk-activity-index', self.index.as_posix(), '--expect-bulk-index-sha256', PIN])
        self.assertLess(at, argv.index('--ccl'))
        self.assertEqual((argv[-1], argv.count('--live')), ('--live', 1))

    def test_without_the_flags_the_collector_command_line_is_exactly_what_it_was(self):
        ctx = cj._context('collect-downstream', RUN, self.env)
        argv = cj.JOBS['collect-downstream'].argv(ctx, self.args())
        self.assertNotIn('--bulk-activity-index', argv)
        self.assertNotIn('--expect-bulk-index-sha256', argv)

    def test_each_refusal_is_a_job_error_and_the_twin_is_accepted(self):
        ctx = cj._context('collect-downstream', RUN, self.env)
        job = cj.JOBS['collect-downstream']
        job.argv(ctx, self.indexed())                                                         # the twin
        bad = {'index without pin': self.args() + ['--bulk-activity-index', self.index.as_posix()],
               'pin without index': self.args() + ['--expect-bulk-index-sha256', PIN],
               'pin short': self.indexed(**{'--expect-bulk-index-sha256': 'abc'}), 'pin upper case': self.indexed(**{'--expect-bulk-index-sha256': PIN.upper()}),
               'pin not hex': self.indexed(**{'--expect-bulk-index-sha256': 'g' * 64}), 'pin 65': self.indexed(**{'--expect-bulk-index-sha256': PIN + 'a'}),
               'index outside inputs': self.indexed(**{'--bulk-activity-index': '/work/index.json'}), 'index traversal': self.indexed(**{'--bulk-activity-index': self.inputs.as_posix() + '/../index.json'}),
               'index given twice': self.indexed() + ['--bulk-activity-index', self.index.as_posix()], 'pin given twice': self.indexed() + ['--expect-bulk-index-sha256', PIN],
               'index flag as value': self.args() + ['--bulk-activity-index', '--live', '--expect-bulk-index-sha256', PIN], 'dangling pin': self.args() + ['--bulk-activity-index', self.index.as_posix(), '--expect-bulk-index-sha256']}
        for name, extra in bad.items():
            with self.subTest(name), self.assertRaises(cj.JobError):
                job.argv(ctx, extra)

    def test_the_messages_name_the_pair_and_the_pin(self):
        ctx = cj._context('collect-downstream', RUN, self.env)
        job = cj.JOBS['collect-downstream']
        with self.assertRaisesRegex(cj.JobError, 'given together or not at all'):
            job.argv(ctx, self.args() + ['--bulk-activity-index', self.index.as_posix()])
        with self.assertRaisesRegex(cj.JobError, '64 lowercase hex digits'):
            job.argv(ctx, self.indexed(**{'--expect-bulk-index-sha256': 'abc'}))
        with self.assertRaisesRegex(cj.JobError, 'must be a file under'):
            job.argv(ctx, self.indexed(**{'--bulk-activity-index': '/work/index.json'}))

    def test_a_refused_indexed_job_exits_2_and_writes_no_receipt(self):
        with mock.patch('sys.stderr'):
            code = cj.main(['run', 'collect-downstream', '--run-id', RUN, '--'] + self.args() + ['--bulk-activity-index', self.index.as_posix()])
        self.assertEqual(code, 2)
        self.assertEqual(list(self.receipts.iterdir()), [])


class Completion(Rig):
    def test_a_receipt_that_names_the_pinned_index_completes(self):
        code, receipt = self.run_job(extra=self.indexed())
        self.assertEqual((code, receipt['status']), (0, 'ok'), receipt and receipt['check'])
        argv = json.loads((self.root / 'argv.json').read_text())
        self.assertEqual(argv[argv.index('--bulk-activity-index'):argv.index('--bulk-activity-index') + 4], ['--bulk-activity-index', self.index.as_posix(), '--expect-bulk-index-sha256', PIN])
        self.assertEqual(json.loads((self.export / CID / ('RECEIPT-' + CID + '.json')).read_text())['bulk_activity_index'], {'sha256': PIN})

    def lie(self, mode, extra):
        code, receipt = self.run_job(extra=extra, mode=mode)
        self.assertEqual((code, receipt['status']), (1, 'failed'), mode)
        self.assertTrue(any('names the pinned bulk index' in problem for problem in receipt['check']['problems']), receipt['check']['problems'])
        self.assertTrue(cj.verify(self.receipts / (RUN + '.json'), 'collect-downstream', RUN), 'the host does not accept it either')

    def test_exit_0_with_no_index_block_in_the_receipt_is_not_completion(self):
        self.lie('no-index-block', self.indexed())

    def test_exit_0_with_another_index_in_the_receipt_is_not_completion(self):
        self.lie('wrong-index', self.indexed())

    def test_an_index_block_in_a_run_that_was_not_given_an_index_is_not_completion(self):
        self.lie('stray-index-block', self.args())

    def test_a_run_without_the_flags_still_completes_and_its_receipt_has_no_block(self):
        code, receipt = self.run_job()
        self.assertEqual((code, receipt['status']), (0, 'ok'))
        self.assertNotIn('bulk_activity_index', json.loads((self.export / CID / ('RECEIPT-' + CID + '.json')).read_text()))


class HostNativeWrapper(dl.Launcher):
    """tools/run_job.ps1 -Job collect-downstream (host-native): the allow-list of -JobArgs. The D075 launcher harness (a copy of the tools tree, a fake docker, fake keys) is reused; its own
    tests are switched off in this subclass."""
    TEXT = (ROOT / 'tools' / 'run_job.ps1').read_text(encoding='utf-8')

    def host_native(self, jobargs):
        (self.state / 'frontier.sqlite').write_bytes(b'')
        return self.run_job('-Job', 'collect-downstream', '-Batch', '4', '-Workers', '2', '-MaxRequests', '30', '-Seeds', str(self.inputs / 'seeds.json'), '-PlanOnly', '-JobArgs', jobargs)

    def test_the_allow_list_names_both_flags_next_to_the_old_ones(self):
        line = [item for item in self.TEXT.splitlines() if item.strip().startswith('$allowedFlags')]
        self.assertEqual(len(line), 1)
        self.assertEqual(re.findall(r"'(--[a-z0-9-]+)'", line[0]),
                         ['--live', '--max-failed-attempts', '--include-later-cycle-context', '--optional-receipts', '--ccl', '--ccl-vintage', '--bulk-activity-index', '--expect-bulk-index-sha256'])
        self.assertEqual(self.TEXT.count('bulk-activity-index'), 1, 'nothing else in the wrapper names the index')

    def test_plan_only_passes_the_pair_through_to_the_collector_arguments(self):
        index = self.inputs / 'index.json'
        index.write_text('{}')
        done = self.host_native(['--live', '--bulk-activity-index', str(index), '--expect-bulk-index-sha256', PIN])
        self.assertEqual(done.returncode, 0, done.stderr)
        arguments = json.loads(done.stdout)['arguments']
        at = arguments.index('--bulk-activity-index')
        self.assertEqual(arguments[at:at + 4], ['--bulk-activity-index', str(index), '--expect-bulk-index-sha256', PIN])
        self.assertEqual(arguments.count('--live'), 1)

    def test_an_unknown_flag_beside_them_is_still_refused(self):
        done = self.host_native(['--live', '--bulk-activity-index', str(self.inputs / 'index.json'), '--expect-bulk-index-sha256', PIN, '--bogus-flag'])
        self.assertEqual(done.returncode, 2)
        self.assertIn("'--bogus-flag' is not accepted", done.stderr)

    def test_without_the_flags_the_plan_is_the_old_one(self):
        done = self.host_native(['--live'])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('--bulk-activity-index', json.loads(done.stdout)['arguments'])


for _name in [name for name in dir(dl.Launcher) if name.startswith('test_') and name not in HostNativeWrapper.__dict__]:
    setattr(HostNativeWrapper, _name, None)      # the inherited D075 launcher tests are not this file's to run


if __name__ == '__main__':
    unittest.main()
