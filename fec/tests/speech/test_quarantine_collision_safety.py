"""Synthetic file-only quarantine tests; no corpus, audio, network or subprocesses."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'fec/speech/quarantine_zero_rms.py'


_DENY = False  # audit hooks cannot be removed; the guard is armed only while a test body runs


def deny_external(event, args):
    if _DENY and event in {'socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('External activity prohibited in this regression')


sys.addaudithook(deny_external)
spec = importlib.util.spec_from_file_location('quarantine_collision_fixture', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QuarantineCollision(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='fec-quarantine-collision-')).resolve()
        self.manifest = self.root / 'clips.jsonl'
        self.quarantine = self.root / 'quarantine'
        self.source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), self.source_hash)
        # Retain tiny synthetic fixtures; never recursively remove a computed path.

    def clip(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def rows(self, paths):
        return [{'outlet': 'HouseLive', 'rms': 0.0, 'out_wav': str(path)} for path in paths]

    def write_manifest(self, rows):
        raw = ''.join(json.dumps(row) + '\n' for row in rows).encode()
        self.manifest.write_bytes(raw)
        return raw

    def run_main(self, expected):
        global _DENY
        _DENY = True
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return module.main(['--manifest', str(self.manifest), '--quarantine', str(self.quarantine), '--expected', str(expected)])
        finally:
            _DENY = False

    def assert_no_move_or_backup(self, original, sources):
        self.assertEqual(self.manifest.read_bytes(), original)
        for path, value in sources:
            self.assertEqual(path.read_bytes(), value)
        self.assertFalse(self.manifest.with_name('clips.pre-zero-rms-20260828.jsonl').exists())
        self.assertFalse(self.manifest.with_name('clips.jsonl.part').exists())
        self.assertFalse(self.quarantine.exists() and any(self.quarantine.iterdir()))

    def test_same_basename_refuses_without_overwrite(self):
        first = self.clip('first/clip.wav', b'first synthetic audio bytes')
        second = self.clip('second/clip.wav', b'second different bytes')
        original = self.write_manifest(self.rows([first, second]))
        with self.assertRaisesRegex(RuntimeError, 'duplicate quarantine'):
            self.run_main(2)
        self.assert_no_move_or_backup(original, [(first, b'first synthetic audio bytes'), (second, b'second different bytes')])

    def test_same_source_alias_refuses_before_partial_move(self):
        first = self.clip('first/clip.wav', b'unchanged duplicate source')
        alias = first.parent / '..' / 'first' / first.name
        original = self.write_manifest(self.rows([first, alias]))
        with self.assertRaisesRegex(RuntimeError, 'duplicate quarantine'):
            self.run_main(2)
        self.assert_no_move_or_backup(original, [(first, b'unchanged duplicate source')])

    @unittest.skipUnless(os.name == 'nt', 'Windows case-insensitive path identity')
    def test_case_only_destination_collision_refuses(self):
        first = self.clip('first/clip.wav', b'lowercase name')
        second = self.clip('second/CLIP.wav', b'uppercase name')
        original = self.write_manifest(self.rows([first, second]))
        with self.assertRaisesRegex(RuntimeError, 'duplicate quarantine'):
            self.run_main(2)
        self.assert_no_move_or_backup(original, [(first, b'lowercase name'), (second, b'uppercase name')])

    def test_unique_moves_preserve_exact_bytes_and_kept_receipt(self):
        first = self.clip('first/one.wav', b'one')
        second = self.clip('second/two.wav', b'two')
        kept = {'outlet': 'Other', 'rms': 1.0, 'out_wav': 'unopened-placeholder'}
        original = self.write_manifest([*self.rows([first, second]), kept])
        self.assertEqual(self.run_main(2), 0)
        self.assertFalse(first.exists()); self.assertFalse(second.exists())
        self.assertEqual((self.quarantine / 'one.wav').read_bytes(), b'one')
        self.assertEqual((self.quarantine / 'two.wav').read_bytes(), b'two')
        self.assertEqual(self.manifest.with_name('clips.pre-zero-rms-20260828.jsonl').read_bytes(), original)
        self.assertEqual([json.loads(line) for line in self.manifest.read_text().splitlines()], [kept])

    def test_expected_count_refuses_before_changes(self):
        first = self.clip('one.wav', b'one')
        original = self.write_manifest(self.rows([first]))
        with self.assertRaisesRegex(RuntimeError, 'expected 2'):
            self.run_main(2)
        self.assert_no_move_or_backup(original, [(first, b'one')])

    def test_existing_target_is_preserved(self):
        first = self.clip('source/one.wav', b'one')
        target = self.clip('quarantine/one.wav', b'previous quarantine')
        original = self.write_manifest(self.rows([first]))
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            self.run_main(1)
        self.assertEqual(first.read_bytes(), b'one')
        self.assertEqual(target.read_bytes(), b'previous quarantine')
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertFalse(self.manifest.with_name('clips.pre-zero-rms-20260828.jsonl').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
