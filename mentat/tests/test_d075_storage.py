"""Inert D075 controls for the container storage and the three downstream jobs (tools/downstream_volume.py, tools/compute_job.py): the in-job export and its hash chain, `verify --map`
from the host, the stored Windows spellings, the volume import, the preflight refusals, the argument table and the completion check (DE-053: the exit code is one input, never the verdict).

The collector child is a STUB that writes the files a real collector writes; the volume, host-state and export folders are temporary directories patched over the container paths.
No Docker, credential, provider or network.
"""
from contextlib import closing
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compute_job as cj  # noqa: E402
import downstream_volume as dv  # noqa: E402
import stored_paths as sp  # noqa: E402

STORED = 'C:\\Users\\pgoff\\Mentat\\mentat\\state\\committee-activity'
CID = 'ab' * 16
RUN = 'downstream-test-1'
SEED_SHA = 'cd' * 32
SEEDS_NAME = 'seeds-' + SEED_SHA + '.json'
TOTALS = 'openfec-unpinned-totals-numbered-v1'


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    return sha_bytes(Path(path).read_bytes())


STUB = r'''
import hashlib, json, os, sys
from pathlib import Path
argv = sys.argv[1:]
get = lambda flag: argv[argv.index(flag) + 1]
assert not [k for k in os.environ if k.startswith(('FEC_API_KEY', 'FEC_KEYS'))], 'a key reached the child'
state, cid, mode = Path(get('--state')), get('--run-id'), os.environ.get('STUB_MODE', 'ok')
Path(os.environ['STUB_ARGV']).write_text(json.dumps(argv), encoding='utf-8')
if mode == 'no-receipt':
    sys.exit(0)
seeds = state / os.environ['STUB_SEEDS_NAME']
seeds.write_bytes(b'{"seed_units": []}')
stored = os.environ['STUB_STORED']
(state / ('RUN-' + cid + '.START.json')).write_text(json.dumps({'run_id': cid, 'seed_receipt': {'captured_copy': stored + '\\' + seeds.name}}), encoding='utf-8')
ledger = state / 'frontier.sqlite'
ledger.write_bytes(ledger.read_bytes() + b'+page-from-run-' + cid.encode())
if mode == 'sidecar':
    (state / 'frontier.sqlite-journal').write_bytes(b'hot')
cap, workers = int(get('--max-requests')), int(get('--workers'))
attempts = cap + 1 if mode == 'over-cap' else 7
receipt = {'ledger': stored + '\\frontier.sqlite', 'raw_archive_directory': stored + '\\responses-' + cid, 'physical_request_cap': cap, 'workers': workers, 'http_attempts': attempts,
           'endpoint_attempts_recorded': attempts, 'candidate_committee_lookup_http_attempts': 0, 'ledger_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest(),
           'transport': {'kind': 'local' if mode == 'local' else 'broker', 'keys_held_by_this_process': 0},
           'stop': {'error_type': 'ArchiveBindingFailed'} if mode == 'integrity' else {'error_type': 'FECAdmissionRefused'}}
(state / ('RECEIPT-' + cid + '.json')).write_text(json.dumps(receipt), encoding='utf-8')
sys.exit(3 if mode == 'crash' else 0)
'''


class Rig(unittest.TestCase):
    """Temporary stand-ins for /frontier, /hoststate, /export, /inputs and /import, a descriptor that matches the mounted tree, and the stored-path map."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='D075-storage-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.frontier, self.hoststate, self.export, self.inputs, self.imported = (self.root / name for name in ('frontier', 'hoststate', 'export', 'inputs', 'import'))
        self.receipts, self.gb = self.root / 'receipts', self.root / 'gb'
        for folder in (self.frontier, self.hoststate, self.export, self.inputs, self.imported, self.receipts, self.gb):
            folder.mkdir()
        self.ledger = self.frontier / 'frontier.sqlite'
        self.ledger.write_bytes(b'SQLite format 3\x00' + bytes(range(256)) * 4000)
        self.base = sha_file(self.ledger)
        (self.inputs / 'seeds.json').write_text('{}')
        self.mapping = '%s=%s;%s=%s;%s=%s\\exports' % (self.hoststate, STORED, self.frontier, STORED, self.export, STORED)
        self.descriptor = self.root / 'request-pool.json'
        self.descriptor.write_text(json.dumps({'adapter_sha256': sha_file(ROOT / 'tools' / 'fec_accounted_client.py'), 'contracts_sha256': sha_file(ROOT / 'tools' / 'spending_endpoint_contracts.py')}))
        self.stub = self.root / 'stub.py'
        self.stub.write_text(STUB, encoding='utf-8')
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(('FEC_API_KEY', 'MENTAT_', 'STUB_'))}, 'MENTAT_COMPUTE_RECEIPT_DIR': str(self.receipts), 'GLASSBOX_EVENTS_DIR': str(self.gb),
                    'MENTAT_STORED_PATH_MAP': self.mapping, 'MENTAT_REQUEST_POOL_CONFIG': str(self.descriptor), 'MENTAT_IMAGE_DIGEST': 'sha256:' + 'e' * 64,
                    'MENTAT_DOWNSTREAM_VOLUME': 'mentat-downstream-frontier', 'STUB_ARGV': str(self.root / 'argv.json'), 'STUB_SEEDS_NAME': SEEDS_NAME, 'STUB_STORED': STORED}
        for patch in (mock.patch.object(cj, 'FRONTIER', self.frontier), mock.patch.object(cj, 'HOSTSTATE', self.hoststate), mock.patch.object(cj, 'EXPORT', self.export),
                      mock.patch.object(cj, 'INPUTS', self.inputs), mock.patch.object(cj, 'IMPORT', self.imported), mock.patch.dict(os.environ, {'MENTAT_STORED_PATH_MAP': self.mapping}),
                      mock.patch.object(cj.glassbox_emit, 'EVENTS_DIR', self.gb)):
            patch.start()
            self.addCleanup(patch.stop)

    def args(self, **replace):
        values = {'--batch': '4', '--collector-run-id': CID, '--expect-base-sha256': self.base, '--seeds': (self.inputs / 'seeds.json').as_posix(), '--max-requests': '300',
                  '--max-failed-attempts': '8', '--workers': '4'}
        values.update(replace)
        return [item for flag, value in values.items() if value is not None for item in (flag, value)] + ['--live']

    def stubbed(self, name='collect-downstream'):
        job = cj.JOBS[name]

        def argv(ctx, extra):
            real = job.argv(ctx, extra)
            real[2] = str(self.stub)
            return real
        return mock.patch.dict(cj.JOBS, {name: dataclasses.replace(job, argv=argv)})

    def run_job(self, extra=None, mode='ok', name='collect-downstream', run_id=RUN):
        env = dict(self.env, STUB_MODE=mode)
        with self.stubbed(name):
            code = cj.run(name, run_id, self.args() if extra is None else extra, env)
        receipt = self.receipts / (run_id + '.json')
        return code, (json.loads(receipt.read_text()) if receipt.is_file() else None)


class Export(Rig):
    def prepare_run(self, with_receipt=True, bound=True):
        (self.frontier / SEEDS_NAME).write_bytes(b'{"seed_units": []}')
        (self.frontier / ('RUN-' + CID + '.START.json')).write_text(json.dumps({'run_id': CID, 'seed_receipt': {'captured_copy': STORED + '\\' + SEEDS_NAME}}))
        (self.frontier / 'frontier.endpoint-contracts.json').write_text('{"v": 1}')
        (self.frontier / 'frontier.endpoint-contracts.v2.json').write_text('{"v": 2}')
        if with_receipt:
            (self.frontier / ('RECEIPT-' + CID + '.json')).write_text(json.dumps({'ledger_sha256': sha_file(self.ledger) if bound else '0' * 64}))

    def test_the_export_is_exclusive_hash_checked_and_ends_with_export_json(self):
        self.prepare_run()
        order = []
        real = dv._copy_exclusive
        with mock.patch.object(dv, '_copy_exclusive', lambda source, target: (order.append(Path(target).name), real(source, target))[1]):
            result = dv.export_run(self.frontier, self.export, CID, image_digest='sha256:' + 'e' * 64, volume='mentat-downstream-frontier')
        self.assertEqual(result['problems'], [])
        names = [item['name'] for item in result['files']]
        self.assertEqual(names, ['frontier.sqlite', 'RUN-' + CID + '.START.json', 'RECEIPT-' + CID + '.json', SEEDS_NAME, 'frontier.endpoint-contracts.json', 'frontier.endpoint-contracts.v2.json', 'EXPORT.json'])
        self.assertEqual(order, names[:-1], 'every copy was made once, and EXPORT.json is written after all of them')
        folder = self.export / CID
        for item in result['files']:
            self.assertEqual(sha_file(folder / item['name']), item['sha256'], item['name'])
        document = json.loads((folder / 'EXPORT.json').read_text())
        now = sha_file(self.ledger)
        self.assertEqual(document['ledger_sha256'], {'before': now, 'copy': now, 'after': now}, 'hash before, of the copy, and after: one value')
        self.assertEqual((document['original_ledger'], document['export_directory']), (STORED + '\\frontier.sqlite', STORED + '\\exports\\' + CID), 'the Windows spellings, not the container paths')
        self.assertEqual((document['volume'], document['image_digest'], document['collector_run_id']), ('mentat-downstream-frontier', 'sha256:' + 'e' * 64, CID))
        self.assertEqual(document['record'], {'name': 'RECEIPT-' + CID + '.json', 'sha256': sha_file(folder / ('RECEIPT-' + CID + '.json'))})
        self.assertEqual(sha_file(folder / 'frontier.sqlite'), sha_file(self.ledger), 'the export is a byte copy of the ledger the receipt binds')
        self.assertEqual(document['problems'], [])

    def test_an_abandoned_run_is_exported_with_its_marker_and_the_unchanged_ledger(self):
        self.prepare_run(with_receipt=False)
        (self.frontier / ('RUN-' + CID + '.ABANDONED.json')).write_text('{"reason": "left nothing"}')
        result = dv.export_run(self.frontier, self.export, CID)
        self.assertEqual(result['problems'], [])
        self.assertIn('RUN-' + CID + '.ABANDONED.json', [item['name'] for item in result['files']])
        self.assertNotIn('RECEIPT-' + CID + '.json', [item['name'] for item in result['files']])

    def refuses(self, text, **options):
        before = sorted(p.name for p in self.export.iterdir())
        result = dv.export_run(self.frontier, self.export, CID, **options)
        self.assertEqual(result['files'], [], text)
        self.assertTrue(any(text in problem for problem in result['problems']), (text, result['problems']))
        self.assertEqual(sorted(p.name for p in self.export.iterdir()), before, 'a refused export writes nothing')

    def test_the_export_refuses_a_crash_an_unbound_ledger_a_missing_receipt_and_an_existing_export(self):
        self.prepare_run()
        (self.frontier / 'frontier.sqlite-wal').write_bytes(b'x')
        self.refuses('journal/WAL sidecar')
        (self.frontier / 'frontier.sqlite-wal').unlink()
        (self.frontier / ('RECEIPT-' + CID + '.json')).write_text(json.dumps({'ledger_sha256': '0' * 64}))
        self.refuses('differs from the receipt it should bind')
        (self.frontier / ('RECEIPT-' + CID + '.json')).unlink()
        self.refuses('no receipt or abandon marker')
        self.prepare_run(with_receipt=False)
        (self.frontier / ('RECEIPT-' + CID + '.json')).write_text(json.dumps({'ledger_sha256': sha_file(self.ledger)}))
        (self.export / CID).mkdir()
        self.refuses('already exists')
        (self.export / CID).rmdir()
        self.assertEqual(dv.export_run(self.frontier, self.export, 'zz')['problems'], ['the collector run id is not 32 hex digits'])
        self.ledger.unlink()
        self.assertEqual(dv.export_run(self.frontier, self.export, CID)['problems'], ['no ledger to export'])

    def test_a_copy_that_does_not_hash_like_its_source_is_flagged_in_the_problems_and_in_export_json(self):
        self.prepare_run()
        real = dv._copy_exclusive

        def corrupting(source, target):
            result = real(source, target)
            if Path(source).name == SEEDS_NAME:
                Path(target).write_bytes(b'bit rot')
            return result
        with mock.patch.object(dv, '_copy_exclusive', corrupting):
            result = dv.export_run(self.frontier, self.export, CID)
        self.assertTrue(any(SEEDS_NAME in problem and 'does not match its source' in problem for problem in result['problems']), result['problems'])
        self.assertEqual(json.loads((self.export / CID / 'EXPORT.json').read_text())['problems'], result['problems'])

    def test_a_ledger_that_changes_while_it_is_copied_is_flagged_not_a_freeze(self):
        self.prepare_run()
        real = dv._copy_exclusive

        def copying(source, target):
            result = real(source, target)
            if Path(source).name == 'frontier.sqlite':
                with open(source, 'ab') as stream:
                    stream.write(b'late write')
            return result
        with mock.patch.object(dv, '_copy_exclusive', copying):
            result = dv.export_run(self.frontier, self.export, CID)
        self.assertTrue(any('changed while it was exported' in problem for problem in result['problems']), result['problems'])
        self.assertTrue((self.export / CID / 'EXPORT.json').is_file(), 'the record of the problem is still written')


class Import(Rig):
    def test_an_empty_volume_is_seeded_exclusively_and_the_hash_is_verified_twice(self):
        snapshot = self.imported / 'frontier.sqlite'
        snapshot.write_bytes(b'SQLite format 3\x00' + b'snapshot' * 5000)
        digest = sha_file(snapshot)
        dest = self.root / 'volume' / 'frontier.sqlite'
        dest.parent.mkdir()
        record = self.root / 'IMPORT.json'
        result = dv.import_ledger(snapshot, dest, digest, record)
        self.assertEqual((result['sha256'], sha_file(dest), json.loads(record.read_text())['sha256']), (digest, digest, digest))
        with self.assertRaises(FileExistsError):
            dv.import_ledger(snapshot, dest, digest)                                  # never overwrites
        self.assertEqual(sha_file(dest), digest)

    def test_a_wrong_hash_a_missing_source_and_sidecars_refuse_and_leave_nothing(self):
        snapshot = self.imported / 'frontier.sqlite'
        snapshot.write_bytes(b'abc')
        dest = self.root / 'volume' / 'frontier.sqlite'
        dest.parent.mkdir()
        with self.assertRaises(ValueError):
            dv.import_ledger(snapshot, dest, '0' * 64)
        with self.assertRaises(ValueError):
            dv.import_ledger(snapshot, dest, 'nothex')
        with self.assertRaises(ValueError):
            dv.import_ledger(self.imported / 'absent.sqlite', dest, sha_bytes(b'abc'))
        (self.root / 'volume' / 'frontier.sqlite-journal').write_bytes(b'x')
        with self.assertRaises(FileExistsError):
            dv.import_ledger(snapshot, dest, sha_bytes(b'abc'))
        self.assertFalse(dest.exists())
        real = dv._copy_exclusive

        def corrupting(source, target):
            real(source, target)
            Path(target).write_bytes(b'corrupt')
            return sha_bytes(b'abc'), 3
        (self.root / 'volume' / 'frontier.sqlite-journal').unlink()
        with mock.patch.object(dv, '_copy_exclusive', corrupting):
            with self.assertRaises(ValueError):
                dv.import_ledger(snapshot, dest, sha_bytes(b'abc'))
        self.assertFalse(dest.exists(), 'a copy that does not hash right is removed')

    def test_the_command_line_exits_0_and_2(self):
        snapshot = self.imported / 'frontier.sqlite'
        snapshot.write_bytes(b'abc')
        dest = self.root / 'v.sqlite'
        with mock.patch('builtins.print') as printed:
            self.assertEqual(dv.main(['import', '--source', str(snapshot), '--dest', str(dest), '--expect-sha256', sha_bytes(b'abc')]), 0)
        self.assertEqual(json.loads(printed.call_args[0][0])['sha256'], sha_bytes(b'abc'))
        with mock.patch('sys.stderr'):
            self.assertEqual(dv.main(['import', '--source', str(snapshot), '--dest', str(dest), '--expect-sha256', sha_bytes(b'abc')]), 2)


class Preflight(Rig):
    def check(self, **options):
        return dv.preflight(self.frontier, self.hoststate, expect_base_sha256=options.pop('expect', self.base), descriptor=options.pop('descriptor', self.descriptor), tools=ROOT / 'tools')

    def test_a_good_base_passes_and_each_refusal_has_its_twin(self):
        self.assertEqual(self.check(), [])
        self.assertTrue(any('not the base the caller expects' in p for p in self.check(expect='0' * 64)))
        self.assertTrue(any('64 lowercase hex' in p for p in self.check(expect='short')))
        (self.frontier / 'frontier.sqlite-journal').write_bytes(b'x')
        self.assertTrue(any('sidecar' in p and 'recover' in p for p in self.check()))
        (self.frontier / 'frontier.sqlite-journal').unlink()
        self.ledger.unlink()
        self.assertTrue(any('never creates one' in p for p in self.check()))

    def test_an_unresolved_run_and_a_foreign_response_folder_refuse(self):
        (self.frontier / ('RUN-' + CID + '.START.json')).write_text('{}')
        self.assertTrue(any('unresolved collector run' in p for p in self.check()))
        (self.frontier / ('RUN-' + CID + '.ABANDONED.json')).write_text('{}')
        self.assertEqual(self.check(), [], 'resolved by its marker')
        (self.hoststate / ('responses-' + 'ef' * 16)).mkdir()
        self.assertTrue(any('unresolved collector run' in p for p in self.check()))
        (self.hoststate / ('RECEIPT-' + 'ef' * 16 + '.json')).write_text('{}')
        self.assertEqual(self.check(), [], 'a host-native batch resolved by its receipt on the host')

    def test_a_descriptor_for_another_tree_or_none_refuses(self):
        value = json.loads(self.descriptor.read_text())
        self.descriptor.write_text(json.dumps(value | {'contracts_sha256': '0' * 64}))
        self.assertTrue(any('contracts_sha256 differs' in p for p in self.check()))
        self.assertTrue(any('missing or unreadable' in p for p in self.check(descriptor=self.root / 'none.json')))
        self.descriptor.write_text('[]')
        self.assertTrue(any('missing or unreadable' in p for p in self.check()))


class Arguments(Rig):
    def test_the_collector_command_line_is_exactly_what_the_job_owns_and_the_caller_gave(self):
        extra = self.args() + ['--enable-endpoint-contract', TOTALS, '--include-later-cycle-context', '--optional-receipts', '--ccl', '2024=/mentat/data/ccl.txt', '--ccl-vintage', '2024=v1',
                               '--priority-candidates', (self.inputs / 'priority.json').as_posix(), '--stop-after-seconds', '900']
        ctx = cj._context('collect-downstream', RUN, self.env)
        argv = cj.JOBS['collect-downstream'].argv(ctx, extra)
        self.assertEqual(argv, [sys.executable, '-B', str(ROOT / 'tools' / 'collect_committee_activity.py'), '--state', self.frontier.as_posix(), '--archive-root', self.hoststate.as_posix(),
                                '--transport', 'broker', '--run-id', CID, '--seeds', (self.inputs / 'seeds.json').as_posix(), '--max-requests', '300', '--max-failed-attempts', '8', '--workers', '4',
                                '--stop-after-seconds', '900', '--enable-endpoint-contract', TOTALS, '--priority-candidates', (self.inputs / 'priority.json').as_posix(),
                                '--ccl', '2024=/mentat/data/ccl.txt', '--ccl-vintage', '2024=v1', '--include-later-cycle-context', '--optional-receipts', '--live'])
        default = cj.JOBS['collect-downstream'].argv(ctx, self.args())
        self.assertEqual(default[default.index('--stop-after-seconds') + 1], str(720 * 60 - 600), 'a graceful deadline ten minutes before the job cap; the child is never killed')
        self.assertEqual(default[-1], '--live')
        self.assertEqual(default.count('--live'), 1)

    def test_every_refusal_is_a_job_error_before_anything_runs_and_the_twin_is_accepted(self):
        ctx = cj._context('collect-downstream', RUN, self.env)
        job = cj.JOBS['collect-downstream']
        job.argv(ctx, self.args())                                                         # the twin
        bad = {'no live': [item for item in self.args() if item != '--live'], 'state': self.args() + ['--state', '/x'], 'archive root': self.args() + ['--archive-root', '/x'],
               'transport': self.args() + ['--transport', 'local'], 'run id': self.args() + ['--run-id', CID], 'init': self.args() + ['--init-new-ledger'],
               'recover': self.args() + ['--recover-crashed-runs'], 'close-out': self.args() + ['--close-out-legacy-run', 'x'], 'migrate': self.args() + ['--migrate-to-schema3', 'a', 'b'],
               'rebind': self.args() + ['--rebind-endpoint-contracts'], 'unknown': self.args() + ['--bogus'], 'positional': self.args() + ['stray'], 'dup': self.args() + ['--workers', '2'],
               'dangling': self.args() + ['--stop-after-seconds'], 'flag as value': self.args() + ['--stop-after-seconds', '--live'],
               'seeds outside inputs': self.args(**{'--seeds': '/etc/seeds.json'}), 'seeds traversal': self.args(**{'--seeds': self.inputs.as_posix() + '/../seeds.json'}),
               'priority outside': self.args() + ['--priority-candidates', '/work/p.json'], 'batch 0': self.args(**{'--batch': '0'}), 'batch text': self.args(**{'--batch': 'x'}),
               'workers 33': self.args(**{'--workers': '33'}), 'workers 0': self.args(**{'--workers': '0'}), 'cap 0': self.args(**{'--max-requests': '0'}),
               'allowance 0': self.args(**{'--max-failed-attempts': '0'}), 'short run id': self.args(**{'--collector-run-id': 'abc'}), 'upper run id': self.args(**{'--collector-run-id': 'AB' * 16}),
               'short sha': self.args(**{'--expect-base-sha256': 'abc'}), 'contract': self.args() + ['--enable-endpoint-contract', 'not a contract'],
               'ccl cycle': self.args() + ['--ccl', '2020=/mentat/x'], 'ccl outside tree': self.args() + ['--ccl', '2024=/etc/passwd'], 'vintage': self.args() + ['--ccl-vintage', '2024='],
               'stop-after 0': self.args() + ['--stop-after-seconds', '0']}
        for flag in cj.DOWN_REQUIRED:
            bad['missing ' + flag] = self.args(**{flag: None})
        for name, extra in bad.items():
            with self.subTest(name), self.assertRaises(cj.JobError):
                job.argv(ctx, extra)

    def test_a_refused_job_exits_2_and_writes_no_receipt(self):
        with mock.patch('sys.stderr'):
            code = cj.main(['run', 'collect-downstream', '--run-id', RUN, '--'] + self.args(**{'--workers': '99'}))
        self.assertEqual(code, 2)
        self.assertEqual(list(self.receipts.iterdir()), [])

    def test_the_plan_names_the_families_the_host_broker_must_admit(self):
        def plan(*extra):
            return cj.downstream_plan('collect-downstream', self.args() + list(extra))
        self.assertEqual(plan()['families'], 'B,E')
        F, H4 = 'openfec-9aa2ee30e63e2071f7a6bbf66d2deb5b32586f6b-F-offset-v1', 'openfec-9aa2ee30e63e2071f7a6bbf66d2deb5b32586f6b-H4-seek-v1'
        lookup = 'openfec-unpinned-candidate-committees-numbered-v1'
        self.assertEqual(plan('--enable-endpoint-contract', F)['families'], 'B,E,F')
        self.assertEqual(plan('--enable-endpoint-contract', H4)['families'], 'B,E,H4')
        self.assertEqual(plan('--enable-endpoint-contract', TOTALS)['families'], 'B,E,totals')
        self.assertEqual(plan('--enable-endpoint-contract', lookup)['families'], 'B,E,candidate_committees')
        self.assertEqual(plan('--optional-receipts')['families'], 'A,B,E')
        everything = plan('--optional-receipts', '--enable-endpoint-contract', lookup, '--enable-endpoint-contract', F, '--enable-endpoint-contract', H4, '--enable-endpoint-contract', TOTALS)
        self.assertEqual((everything['families'], everything['collector_run_id'], everything['max_requests'], everything['workers'], everything['batch']), ('A,B,E,F,H4,totals,candidate_committees', CID, 300, 4, 4))
        with self.assertRaises(cj.JobError):
            plan('--enable-endpoint-contract', 'openfec-unknown-v1')
        self.assertEqual(cj.downstream_plan('collect-downstream-recover', ['--collector-run-id', CID])['collector_run_id'], CID)
        self.assertEqual(cj.downstream_plan('collect-downstream-import', ['--expect-sha256', self.base])['expect_sha256'], self.base)
        with self.assertRaises(cj.JobError):
            cj.downstream_plan('collect-downstream-recover', ['--collector-run-id', CID, '--live'])
        with mock.patch('builtins.print') as printed:
            self.assertEqual(cj.main(['downstream-plan', '--job', 'collect-downstream', '--'] + self.args()), 0)
            self.assertTrue(json.loads(printed.call_args[0][0])['ok'])
            self.assertEqual(cj.main(['downstream-plan', '--job', 'collect-downstream', '--', '--batch', '4']), 2)
            self.assertFalse(json.loads(printed.call_args[0][0])['ok'])

    def test_the_dry_run_plans_without_a_key_and_says_the_job_needs_a_broker(self):
        with mock.patch('builtins.print') as printed:
            self.assertEqual(cj.run('collect-downstream', RUN, self.args(), self.env, dry_run=True), 0)
        plan = json.loads(printed.call_args[0][0])
        self.assertEqual((plan['network'], plan['keys'], plan['broker'], plan['minutes']), (True, None, True, 720))
        self.assertTrue(plan['outputs'][0].endswith('frontier.sqlite'))
        self.assertEqual(list(self.receipts.iterdir()), [], 'a dry run touches nothing, not even the volume')
        with mock.patch('builtins.print') as printed:
            self.assertEqual(cj.main(['jobs']), 0)
        jobs = json.loads(printed.call_args[0][0])
        self.assertEqual({name: job['broker'] for name, job in jobs.items() if job['broker']}, {'collect-downstream': True})


class Reconcile(Rig):
    """Review F3: after the broker exits, its charge to the shared ledger, its journal and the collector receipt must agree."""
    def files(self, *, charged=3, journalled=3, lines=3, counted=3, undelivered=0, broker=True, receipt=True, abandoned=False, journal=True, run_id=CID):
        state = self.hoststate
        (state / 'broker').mkdir(exist_ok=True)
        if journal:
            (state / 'broker' / (run_id + '.attempts.ndjson')).write_text(''.join(json.dumps({'event': 'attempt', 'n': n + 1}) + '\n' + json.dumps({'event': 'delivery', 'n': n + 1, 'delivered': True}) + '\n'
                                                                                     for n in range(lines)))
        if broker:
            (state / 'broker' / ('BROKER-' + run_id + '.json')).write_text(json.dumps({'physical_attempts_charged': charged, 'attempts_journalled': journalled, 'undelivered_attempts': list(range(undelivered))}))
        folder = state / 'exports' / run_id
        folder.mkdir(parents=True, exist_ok=True)
        if receipt:
            (folder / ('RECEIPT-' + run_id + '.json')).write_text(json.dumps({'http_attempts': counted}))
        if abandoned:
            (folder / ('RUN-' + run_id + '.ABANDONED.json')).write_text('{}')

    def test_agreeing_counts_pass_with_the_undelivered_allowance_and_for_an_abandoned_run(self):
        self.files()
        result = dv.reconcile_broker(self.hoststate, CID)
        self.assertEqual((result['ok'], result['problems'], result['charged'], result['journalled'], result['http_attempts'], result['undelivered']), (True, [], 3, 3, 3, 0))
        self.files(counted=2, undelivered=1, run_id='cd' * 16)                      # one answer never reached the collector: the journal is ahead of the receipt by exactly that
        self.assertTrue(dv.reconcile_broker(self.hoststate, 'cd' * 16)['ok'])
        self.files(charged=0, journalled=0, lines=0, counted=0, receipt=False, abandoned=True, run_id='ef' * 16)
        self.assertTrue(dv.reconcile_broker(self.hoststate, 'ef' * 16)['ok'])

    def test_every_gap_between_the_charge_the_journal_and_the_receipt_is_a_problem(self):
        cases = {'charged but never journalled (the F1/F2 shape)': (dict(charged=4), 'have no record'),
                 'the journal file holds fewer attempts than the broker receipt says': (dict(lines=2), 'journal file holds 2'),
                 'the journal file is missing': (dict(journal=False), 'journal file holds None'),
                 'the receipt counts fewer than were journalled': (dict(counted=2), 'counts 2 plus 0 undelivered'),
                 'the receipt counts more than were journalled': (dict(counted=4), 'counts 4 plus 0 undelivered'),
                 'an undelivered attempt the receipt already counts': (dict(undelivered=1), 'counts 3 plus 1 undelivered'),
                 'no broker receipt': (dict(broker=False), 'no readable broker receipt'),
                 'no exported collector receipt': (dict(receipt=False), 'no exported collector receipt')}
        for index, (name, (options, fragment)) in enumerate(cases.items(), start=1):
            with self.subTest(name):
                run = '%02x' % (0x60 + index) * 16
                self.files(run_id=run, **options)
                result = dv.reconcile_broker(self.hoststate, run)
                self.assertFalse(result['ok'])
                self.assertTrue(any(fragment in problem for problem in result['problems']), (fragment, result['problems']))

    def test_counts_that_are_not_counts_and_a_bad_run_id_are_refused(self):
        self.files()
        broker = self.hoststate / 'broker' / ('BROKER-' + CID + '.json')
        for bad in ({'physical_attempts_charged': '3', 'attempts_journalled': 3, 'undelivered_attempts': []}, {'physical_attempts_charged': 3, 'attempts_journalled': True, 'undelivered_attempts': []},
                    {'physical_attempts_charged': 3, 'attempts_journalled': 3, 'undelivered_attempts': 0}, {'physical_attempts_charged': -1, 'attempts_journalled': 3, 'undelivered_attempts': []}):
            broker.write_text(json.dumps(bad))
            self.assertFalse(dv.reconcile_broker(self.hoststate, CID)['ok'], bad)
        broker.write_text('not json')
        self.assertFalse(dv.reconcile_broker(self.hoststate, CID)['ok'])
        self.assertFalse(dv.reconcile_broker(self.hoststate, 'xyz')['ok'])

    def test_the_command_line_answers_json_and_exits_1_on_a_gap(self):
        import contextlib
        import io
        self.files()
        self.files(charged=5, run_id='cd' * 16)
        for run, code in ((CID, 0), ('cd' * 16, 1)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(dv.main(['reconcile', '--state', str(self.hoststate), '--run-id', run]), code)
            self.assertEqual(json.loads(out.getvalue())['ok'], code == 0)


class Restore(Rig):
    """Review F4: lose the volume, import the last export, and the next live start must not be refused for the earlier runs' response folders."""
    def setUp(self):
        super().setUp()
        # D3: the restore reads the exported ledger's pages table, so the stand-in ledger is a real (tiny) SQLite file whose pages name the response folders, as the collector's do
        self.ledger.unlink()
        with closing(sqlite3.connect(self.ledger)) as db:
            db.execute('CREATE TABLE pages(committee_id TEXT NOT NULL, page_no INTEGER NOT NULL, archive_path TEXT NOT NULL, PRIMARY KEY(committee_id, page_no))')
            db.commit()

    def run_and_export(self, cid, content, *, abandoned=False, pages=1):
        """A run as the job leaves it: START and RECEIPT (or an abandon marker) in the volume, the in-job export under <state>/exports/<id>/, and its response folder on the host."""
        if not abandoned:
            with closing(sqlite3.connect(self.ledger)) as db:
                for number in range(pages):
                    db.execute('INSERT INTO pages VALUES (?,?,?)', (content.decode(), number, STORED + '\\responses-' + cid + '\\page-%d.body.bin' % number))
                db.commit()
            (self.frontier / ('RUN-' + cid + '.START.json')).write_text(json.dumps({'run_id': cid, 'seed_receipt': {}}))
            (self.frontier / ('RECEIPT-' + cid + '.json')).write_text(json.dumps({'ledger_sha256': sha_file(self.ledger), 'http_attempts': 1, 'new_pages_this_invocation': pages}))
        else:
            (self.frontier / ('RUN-' + cid + '.START.json')).write_text(json.dumps({'run_id': cid, 'seed_receipt': {}}))
            (self.frontier / ('RUN-' + cid + '.ABANDONED.json')).write_text(json.dumps({'run_id': cid}))
        result = dv.export_run(self.frontier, self.hoststate / 'exports', cid)
        self.assertEqual(result['problems'], [])
        (self.hoststate / ('responses-' + cid)).mkdir()
        time.sleep(0.02)                                                                       # exported_utc orders the runs
        return sha_file(self.ledger)

    def lose_the_volume(self, export_id, expect):
        """A brand-new empty volume seeded from one export, as collect-downstream-import does."""
        fresh = self.root / 'fresh-volume'
        shutil.rmtree(fresh, ignore_errors=True)
        fresh.mkdir()
        dv.import_ledger(self.hoststate / 'exports' / export_id / 'frontier.sqlite', fresh / 'frontier.sqlite', expect)
        return fresh

    def test_after_losing_the_volume_and_importing_the_last_export_the_next_live_start_is_not_refused(self):
        a, b, c = 'a1' * 16, 'b2' * 16, 'c3' * 16
        self.run_and_export(a, b'+run a')
        self.run_and_export(b, b'+abandoned', abandoned=True)
        last = self.run_and_export(c, b'+run c')
        fresh = self.lose_the_volume(c, last)
        before = dv.preflight(fresh, self.hoststate, expect_base_sha256=last)
        self.assertEqual(len(before), 1)
        self.assertIn('unresolved collector run(s)', before[0])
        for run in (a, b, c):
            self.assertIn(run, before[0], 'every earlier container run looks like a legacy crash after the volume is lost: the live start is refused')
        result = dv.restore_receipts(self.hoststate, last)
        self.assertEqual((sorted(result['restored']), result['problems'], result['anchor'], result['not_restored']), (sorted([a, b, c]), [], c, []))
        self.assertTrue((self.hoststate / ('RECEIPT-' + a + '.json')).is_file() and (self.hoststate / ('RECEIPT-' + c + '.json')).is_file())
        self.assertTrue((self.hoststate / ('RUN-' + b + '.ABANDONED.json')).is_file(), 'an abandon marker resolves a run too')
        for run, name in ((a, 'RECEIPT-' + a + '.json'), (c, 'RECEIPT-' + c + '.json')):
            self.assertEqual(sha_file(self.hoststate / name), sha_file(self.hoststate / 'exports' / run / name), 'byte for byte the exported record')
        self.assertEqual(dv.preflight(fresh, self.hoststate, expect_base_sha256=last), [], 'the next live start is not refused')
        again = dv.restore_receipts(self.hoststate, last)
        self.assertEqual((again['restored'], sorted(again['already_present']), again['problems']), ([], sorted([a, b, c]), []), 'idempotent')

    def test_a_run_newer_than_the_imported_ledger_stays_unresolved_for_a_human(self):
        a, b = 'a1' * 16, 'b2' * 16
        older = self.run_and_export(a, b'+run a')
        self.run_and_export(b, b'+run b')
        fresh = self.lose_the_volume(a, older)                                                  # an OLDER export is imported: run b's pages are not in this ledger
        result = dv.restore_receipts(self.hoststate, older)
        self.assertEqual((result['restored'], result['anchor']), ([a], a))
        self.assertEqual([item['run_id'] for item in result['not_restored']], [b])
        self.assertIn('exported after the imported ledger', result['not_restored'][0]['reason'])
        self.assertFalse((self.hoststate / ('RECEIPT-' + b + '.json')).exists())
        problems = dv.preflight(fresh, self.hoststate, expect_base_sha256=older)
        self.assertEqual(len(problems), 1)
        self.assertIn(b, problems[0])
        self.assertNotIn(a, problems[0])

    def test_an_export_that_does_not_verify_is_never_restored(self):
        a, b = 'a1' * 16, 'b2' * 16
        self.run_and_export(a, b'+run a')
        last = self.run_and_export(b, b'+run b')
        tampered = self.hoststate / 'exports' / a / ('RECEIPT-' + a + '.json')
        tampered.write_text(json.dumps({'ledger_sha256': '0' * 64, 'http_attempts': 99}))
        fresh = self.lose_the_volume(b, last)
        result = dv.restore_receipts(self.hoststate, last)
        self.assertEqual(result['restored'], [b])
        self.assertEqual([(item['run_id'], 'does not hash' in item['reason']) for item in result['not_restored']], [(a, True)])
        self.assertFalse((self.hoststate / ('RECEIPT-' + a + '.json')).exists())
        self.assertIn(a, dv.preflight(fresh, self.hoststate, expect_base_sha256=last)[0])
        (self.hoststate / 'exports' / b / 'EXPORT.json').write_text('{}')
        self.assertEqual(dv.restore_receipts(self.hoststate, last)['restored'], [], 'an export record that does not name its run restores nothing')

    def test_a_different_file_already_in_the_state_folder_is_never_overwritten_and_an_unrelated_ledger_restores_nothing(self):
        a = 'a1' * 16
        last = self.run_and_export(a, b'+run a')
        (self.hoststate / ('RECEIPT-' + a + '.json')).write_text('{"someone else": true}')
        result = dv.restore_receipts(self.hoststate, last)
        self.assertEqual((result['restored'], len(result['problems'])), ([], 1))
        self.assertEqual((self.hoststate / ('RECEIPT-' + a + '.json')).read_text(), '{"someone else": true}')
        self.assertIn('differs from the export', result['problems'][0])
        unrelated = dv.restore_receipts(self.hoststate, 'f' * 64)
        self.assertEqual((unrelated['restored'], unrelated['problems'], unrelated['anchor']), ([], [], None))
        self.assertEqual(unrelated['not_restored'][0]['run_id'], a)
        self.assertEqual(dv.restore_receipts(self.hoststate, 'nothex')['problems'], ['--expect-sha256 must be 64 lowercase hex digits'])
        self.assertEqual(dv.restore_receipts(self.root / 'no-such-state', last)['restored'], [])

    def test_after_a_branched_import_a_run_whose_pages_are_not_in_the_imported_ledger_is_not_restored(self):
        # delta review D3 (probe P2): the time rule alone restored b's receipt, which binds a ledger (a+b) that is not an ancestor of the imported one (a+c)
        a, b, c = 'a1' * 16, 'b2' * 16, 'c3' * 16
        older = self.run_and_export(a, b'+run a')
        self.run_and_export(b, b'+run b')
        self.lose_the_volume(a, older)                                                      # the OLDER export is imported: the lineage branches here
        self.assertEqual(dv.restore_receipts(self.hoststate, older)['restored'], [a])
        shutil.copyfile(self.hoststate / 'exports' / a / 'frontier.sqlite', self.ledger)      # run c continues from the a-only ledger, not from a+b
        last = self.run_and_export(c, b'+run c')
        marker = self.hoststate / ('RUN-' + b + '.ABANDONED.json')
        for resolved_by_a_human in (False, True):
            with self.subTest(resolved_by_a_human=resolved_by_a_human):
                if resolved_by_a_human:
                    marker.write_text(json.dumps({'run_id': b}))
                self.lose_the_volume(c, last)
                result = dv.restore_receipts(self.hoststate, last)
                self.assertEqual((result['problems'], result['anchor'], sorted(result['restored'] + result['already_present'])), ([], c, sorted([a, c])))
                self.assertEqual([item['run_id'] for item in result['not_restored']], [b])
                self.assertIn('already resolves this run' if resolved_by_a_human else 'pages are not in the imported ledger', result['not_restored'][0]['reason'])
                self.assertFalse((self.hoststate / ('RECEIPT-' + b + '.json')).exists(), 'no second resolution record for b')
        self.assertEqual(json.loads(marker.read_text()), {'run_id': b}, 'the human record is untouched')

    def test_a_receipt_that_declares_no_new_pages_keeps_the_time_rule_and_an_unreadable_ledger_restores_nothing_that_needs_pages(self):
        a, z = 'a1' * 16, 'd4' * 16
        self.run_and_export(a, b'+run a')
        last = self.run_and_export(z, b'+no pages', pages=0)
        self.lose_the_volume(z, last)
        with mock.patch.object(dv, '_runs_with_pages', side_effect=sqlite3.DatabaseError('file is not a database')):
            result = dv.restore_receipts(self.hoststate, last)
        self.assertEqual((result['restored'], result['problems']), ([z], []), 'a run that added no pages needs none in the ledger')
        self.assertEqual([item['run_id'] for item in result['not_restored']], [a])
        self.assertIn('could not be read for its pages (file is not a database)', result['not_restored'][0]['reason'])
        self.assertFalse((self.hoststate / ('RECEIPT-' + a + '.json')).exists(), 'fail closed: what cannot be confirmed stays unresolved')
        again = dv.restore_receipts(self.hoststate, last)
        self.assertEqual((sorted(again['restored']), again['not_restored']), ([a], []), 'the same call restores a once the ledger can be read')

    def test_a_file_that_wins_the_exclusive_create_race_is_reported_and_never_deleted(self):
        # delta review D2 (probe P3): the foreign file appears between the exists() check and the exclusive create
        a = 'a1' * 16
        last = self.run_and_export(a, b'+run a')
        target = self.hoststate / ('RECEIPT-' + a + '.json')
        real = dv._copy_exclusive

        def raced(source, destination):
            Path(destination).write_text('{"someone else": true}')
            return real(source, destination)                                                 # open(..., 'xb') raises FileExistsError
        with mock.patch.object(dv, '_copy_exclusive', raced):
            result = dv.restore_receipts(self.hoststate, last)
        self.assertEqual((result['restored'], len(result['problems'])), ([], 1))
        self.assertIn('appeared in the state folder', result['problems'][0])
        self.assertEqual(target.read_text(), '{"someone else": true}', 'the other writer\'s file is still there')

    def test_the_command_line_exits_1_on_a_problem_and_0_otherwise(self):
        import contextlib
        import io
        a = 'a1' * 16
        last = self.run_and_export(a, b'+run a')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(dv.main(['restore-receipts', '--state', str(self.hoststate), '--expect-sha256', last]), 0)
        self.assertEqual(json.loads(out.getvalue())['restored'], [a])
        (self.hoststate / ('RECEIPT-' + a + '.json')).write_text('x')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dv.main(['restore-receipts', '--state', str(self.hoststate), '--expect-sha256', last]), 1)


class JobFlow(Rig):
    def test_a_clean_run_exports_and_verifies_from_the_host_through_the_export_map(self):
        code, receipt = self.run_job()
        self.assertEqual((code, receipt['status'], receipt['check']), (0, 'ok', {'ok': True, 'problems': []}), receipt and receipt['check'])
        self.assertFalse(receipt['keys'], 'the container job holds no key')
        names = sorted(Path(item['path']).name for item in receipt['outputs'])
        self.assertEqual(names, sorted(['frontier.sqlite', 'RUN-' + CID + '.START.json', 'RECEIPT-' + CID + '.json', SEEDS_NAME, 'EXPORT.json']))
        argv = json.loads((self.root / 'argv.json').read_text())
        self.assertEqual((argv[argv.index('--transport') + 1], argv[argv.index('--run-id') + 1], argv[-1]), ('broker', CID, '--live'))
        # the host side: the receipt as a Linux container wrote it ('/export/...'), verified with --map /export=<host exports folder>
        host = self.root / 'host-exports'
        shutil.copytree(self.export, host)
        text = (self.receipts / (RUN + '.json')).read_text().replace(json.dumps(str(self.export))[1:-1], '/export').replace('\\\\', '/')
        linux = self.root / 'linux-receipt.json'
        linux.write_text(text)
        self.assertEqual(cj.verify(linux, 'collect-downstream', RUN, [('/export', str(host))]), [])
        with mock.patch('builtins.print') as printed:
            self.assertEqual(cj.main(['verify', '--receipt', str(linux), '--job', 'collect-downstream', '--run-id', RUN, '--map', '/export=' + host.as_posix()]), 0)
        self.assertEqual(json.loads(printed.call_args[0][0]), {'ok': True, 'problems': []})
        (host / CID / 'frontier.sqlite').write_bytes(b'tampered after the run')
        self.assertTrue(any('changed or missing' in p for p in cj.verify(linux, 'collect-downstream', RUN, [('/export', str(host))])))
        (host / CID / 'frontier.sqlite').unlink()
        self.assertTrue(any('changed or missing' in p for p in cj.verify(linux, 'collect-downstream', RUN, [('/export', str(host))])))
        self.assertTrue(cj.verify(linux, 'collect-downstream', RUN), 'without the map the container path does not exist on the host')
        document = json.loads((self.export / CID / 'EXPORT.json').read_text())
        self.assertEqual(document['original_ledger'], STORED + '\\frontier.sqlite')
        self.assertEqual(json.loads((self.export / CID / ('RECEIPT-' + CID + '.json')).read_text())['ledger'], document['original_ledger'])

    def lie(self, mode, fragment):
        code, receipt = self.run_job(mode=mode)
        self.assertEqual((code, receipt['status']), (1, 'failed'), mode)
        self.assertTrue(any(fragment in problem for problem in receipt['check']['problems']), (mode, receipt['check']['problems']))
        self.assertTrue(cj.verify(self.receipts / (RUN + '.json'), 'collect-downstream', RUN), 'the host does not accept it either')

    def test_exit_0_without_a_receipt_is_not_completion(self):
        self.lie('no-receipt', 'no receipt or abandon marker')

    def test_exit_0_with_an_integrity_stop_is_not_completion(self):
        self.lie('integrity', 'stop is not an integrity failure')

    def test_exit_0_over_the_physical_cap_is_not_completion(self):
        self.lie('over-cap', 'attempts are within the cap')

    def test_exit_0_of_a_run_that_did_not_go_through_the_broker_is_not_completion(self):
        self.lie('local', 'through the broker')

    def test_exit_0_that_left_a_hot_journal_is_not_completion_and_is_not_exported(self):
        self.lie('sidecar', 'sidecar')
        self.assertFalse((self.export / CID).exists())

    def test_a_child_that_dies_after_writing_its_receipt_still_leaves_the_export_for_the_record(self):
        code, receipt = self.run_job(mode='crash')
        self.assertEqual((code, receipt['status'], receipt['exit_code']), (3, 'failed', 3))
        self.assertTrue((self.export / CID / 'EXPORT.json').is_file(), 'the export is made whatever the exit code was')
        self.assertIn('frontier.sqlite', [Path(item['path']).name for item in receipt['outputs']])

    def test_an_export_that_fails_with_an_io_error_still_writes_a_failed_receipt(self):
        with mock.patch.object(dv, '_copy_exclusive', side_effect=OSError(28, 'No space left on device')):
            code, receipt = self.run_job()
        self.assertEqual((code, receipt and receipt['status']), (1, 'failed'))
        self.assertTrue(any('No space left on device' in problem for problem in receipt['check']['problems']), receipt['check']['problems'])

    def test_the_preflight_refuses_before_the_child_starts_and_writes_nothing(self):
        for name, change in {'a ledger that is not the expected base': dict(args=self.args(**{'--expect-base-sha256': '0' * 64})),
                             'no stored map': dict(env={'MENTAT_STORED_PATH_MAP': ''}), 'no descriptor': dict(env={'MENTAT_REQUEST_POOL_CONFIG': ''})}.items():
            with self.subTest(name):
                env = dict(self.env, **change.get('env', {}))
                with mock.patch.dict(os.environ, {'MENTAT_STORED_PATH_MAP': env['MENTAT_STORED_PATH_MAP']}), self.stubbed(), self.assertRaises(cj.JobError):
                    cj.run('collect-downstream', RUN, change.get('args') or self.args(), env)
        self.assertFalse((self.root / 'argv.json').exists(), 'the collector never started')
        self.assertEqual((list(self.export.iterdir()), list(self.receipts.iterdir())), ([], []))

    def test_the_command_line_entry_turns_a_refusal_into_exit_2(self):
        with mock.patch('sys.stderr'), self.stubbed():
            code = cj.main(['run', 'collect-downstream', '--run-id', RUN, '--'] + self.args(**{'--expect-base-sha256': '0' * 64}))
        self.assertEqual(code, 2)

    def test_recovery_needs_the_one_unresolved_run_and_exports_what_it_leaves(self):
        recover = ['--collector-run-id', CID]
        with mock.patch('sys.stderr'), self.assertRaises(cj.JobError):
            cj.run('collect-downstream-recover', RUN, recover, self.env)                  # nothing is unresolved
        (self.frontier / ('RUN-' + CID + '.START.json')).write_text('{}')
        with self.assertRaises(cj.JobError):
            cj.run('collect-downstream-recover', RUN, ['--collector-run-id', 'cd' * 16], self.env)   # not the unresolved one
        script = self.root / 'recover_stub.py'
        script.write_text("import json, os, sys\nfrom pathlib import Path\nstate = Path(sys.argv[sys.argv.index('--state') + 1])\n"
                          "import hashlib\nledger = state / 'frontier.sqlite'\ncid = os.environ['STUB_CID']\n"
                          "(state / ('RECEIPT-' + cid + '.json')).write_text(json.dumps({'recovered_after_crash': True, 'ledger_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest()}))\n", encoding='utf-8')
        job = cj.JOBS['collect-downstream-recover']
        with mock.patch.dict(cj.JOBS, {'collect-downstream-recover': dataclasses.replace(job, argv=lambda ctx, extra: [sys.executable, str(script), *job.argv(ctx, extra)[3:]])}):
            code = cj.run('collect-downstream-recover', RUN, recover, dict(self.env, STUB_CID=CID))
        receipt = json.loads((self.receipts / (RUN + '.json')).read_text())
        self.assertEqual((code, receipt['status']), (0, 'ok'), receipt['check'])
        self.assertTrue((self.export / CID / ('RECEIPT-' + CID + '.json')).is_file())

    def test_the_import_job_seeds_the_volume_and_its_record_verifies(self):
        self.ledger.unlink()
        (self.imported / 'frontier.sqlite').write_bytes(b'SQLite format 3\x00' + b'migrated schema 3' * 2000)
        digest = sha_file(self.imported / 'frontier.sqlite')
        code = cj.run('collect-downstream-import', RUN, ['--expect-sha256', digest], self.env)
        receipt = json.loads((self.receipts / (RUN + '.json')).read_text())
        self.assertEqual((code, receipt['status'], receipt['check']['problems']), (0, 'ok', []))
        self.assertEqual(sha_file(self.ledger), digest)
        self.assertEqual(cj.verify(self.receipts / (RUN + '.json'), 'collect-downstream-import', RUN), [])
        self.assertEqual(json.loads(Path(receipt['outputs'][0]['path']).read_text())['sha256'], digest)
        again = cj.run('collect-downstream-import', 'import-2', ['--expect-sha256', digest], self.env)
        self.assertNotEqual(again, 0, 'an import never overwrites a ledger')
        self.assertEqual(sha_file(self.ledger), digest)
        with self.assertRaises(cj.JobError):
            cj.run('collect-downstream-import', 'import-3', ['--expect-sha256', 'short'], self.env)


if __name__ == '__main__':
    unittest.main()
