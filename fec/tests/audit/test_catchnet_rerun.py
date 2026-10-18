"""Temporary observer receipts only; no collection, network, or production output."""
import contextlib
import datetime
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'fec/audit/audit_congress_full_text_failure_catchnet.py'


_DENY = False  # audit hooks cannot be removed; the guard is armed only while a test body runs


def deny_external(event, args):
    if _DENY and event in {'socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('External activity forbidden in catchnet fixture')


sys.addaudithook(deny_external)
spec = importlib.util.spec_from_file_location('catchnet_rerun_fixture', SOURCE)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class Clock:
    tick = 0

    @classmethod
    def now(cls, zone):
        cls.tick += 1
        return datetime.datetime(2026, 1, 1, tzinfo=zone) + datetime.timedelta(seconds=cls.tick)


class CatchnetRerun(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fec-catchnet-rerun-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.run = self.root / 'run'
        failure_dir = self.run / 'failure_attempts'
        failure_dir.mkdir(parents=True)
        self.discovery = self.root / 'discovery.ndjson'
        self.discovery.write_text(json.dumps({'canonical_key': '119:hr:1', 'canonical_url': 'https://www.congress.gov/bill/119th-congress/house-bill/1'}) + '\n', encoding='utf-8')
        (failure_dir / 'failure.json').write_text(json.dumps({'canonical_key': '119:hr:1', 'reason': 'synthetic failure'}), encoding='utf-8')
        self.out = self.root / 'observer-output'
        self.source_hash = M.sha256_file(SOURCE)
        self.addCleanup(lambda: self.assertEqual(M.sha256_file(SOURCE), self.source_hash))

    def execute(self):
        global _DENY
        args = SimpleNamespace(run=self.run, discovery=self.discovery, out=self.out)
        _DENY = True
        try:
            with mock.patch.object(M, 'parse_args', return_value=args), mock.patch.object(M, 'datetime', Clock), contextlib.redirect_stdout(io.StringIO()):
                return M.main()
        finally:
            _DENY = False

    def snapshot(self):
        pointer = json.loads((self.out / 'current.json').read_bytes())
        return pointer, Path(pointer['manifest']['path'])

    def test_identical_rerun_reuses_exact_immutable_snapshot(self):
        self.assertEqual(self.execute(), 0)
        pointer, manifest_path = self.snapshot()
        before = {p.name: p.read_bytes() for p in manifest_path.parent.iterdir() if p.is_file()}
        self.assertEqual(pointer['status'], 'PASS_RECEIPT_LEVEL_WITH_UNRESOLVED_FAILURES')
        self.assertEqual(self.execute(), 0)
        current, current_path = self.snapshot()
        self.assertEqual(current_path, manifest_path)
        self.assertEqual(current['manifest'], pointer['manifest'])
        self.assertNotEqual(current['updated_at'], pointer['updated_at'])
        self.assertEqual({p.name: p.read_bytes() for p in manifest_path.parent.iterdir() if p.is_file()}, before)

    def test_altered_snapshot_manifest_is_refused_and_preserved(self):
        self.execute()
        pointer_before = (self.out / 'current.json').read_bytes()
        _, manifest_path = self.snapshot()
        old = json.loads(manifest_path.read_bytes())
        old['unresolved_failure_row_count'] = 999
        manifest_path.write_bytes(M.json_body(old))
        before = manifest_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'immutable catchnet collision'):
            self.execute()
        self.assertEqual(manifest_path.read_bytes(), before)
        self.assertEqual((self.out / 'current.json').read_bytes(), pointer_before)

    def test_corrupted_snapshot_output_is_refused_and_preserved(self):
        self.execute()
        pointer_before = (self.out / 'current.json').read_bytes()
        _, manifest_path = self.snapshot()
        artifact = manifest_path.parent / 'failure_attempts.ndjson'
        artifact.write_bytes(b'changed synthetic evidence\n')
        with self.assertRaisesRegex(ValueError, 'immutable catchnet collision'):
            self.execute()
        self.assertEqual(artifact.read_bytes(), b'changed synthetic evidence\n')
        self.assertEqual((self.out / 'current.json').read_bytes(), pointer_before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
