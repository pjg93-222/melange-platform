"""Temporary HTML/ZIP fixtures only; no generated page or live package access."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(os.environ.get('FEC_SURFACE_SOURCE_ROOT', Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(ROOT / 'fec'))
from surfaces import make_email_package as package
from surfaces import honors_publication


class EmailSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='email-snapshot-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'outputs/email_package'
        self.out.mkdir(parents=True)
        self.names = [name for name, _ in package.entries(package.FAMILY_FILES) + package.entries(package.MONOLITH_FILES)]
        self.original = {name: ('<html>fixture ' + name + '</html>').encode() for name in self.names}
        for name, data in self.original.items():
            (self.root / name).write_bytes(data)
        self.deliveries = [self.out / 'Moneyball_Email_Package.zip',
                           self.out / 'Moneyball_Email_Package_AllInOne.zip',
                           self.root / 'data/money/Moneyball_email.zip',
                           self.out / 'EMAIL_PACKAGE_MANIFEST.json']
        for path in self.deliveries:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('previous:' + path.name).encode())
        self.before = {path: path.read_bytes() for path in self.deliveries}

    def gate(self, root, names, captured=None):
        for name in names:
            content = captured[name] if captured is not None else (Path(root) / name).read_text(encoding='utf-8')
            self.assertEqual(content.encode(), self.original[name])
        return 'fixture_generation'

    def preserved(self):
        self.assertEqual({path: path.read_bytes() for path in self.deliveries}, self.before)
        self.assertFalse(list(self.out.glob('.email-package-*')))

    def test_source_drift_after_zip_refuses_before_any_delivery_replacement(self):
        original_build = package.build_zip
        calls = []

        def changing_build(destination, root, names):
            original_build(destination, root, names)
            calls.append(destination)
            if len(calls) == 2:
                (self.root / package.FAMILY_FILES[0]).write_bytes(b'different live page')

        with mock.patch.object(honors_publication, 'validate_pages', self.gate), mock.patch.object(package, 'build_zip', changing_build):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                package.build(self.root, self.out)
        self.preserved()

    def test_receipt_drift_refuses_before_delivery_replacement(self):
        receipt = self.root / 'data/engine/collection_intake_receipt.json'
        receipt.parent.mkdir(parents=True)
        receipt.write_bytes(b'{"fixture":1}')
        original_build = package.build_zip

        def changing_build(destination, root, names):
            original_build(destination, root, names)
            receipt.write_bytes(b'{"fixture":2}')

        with mock.patch.object(honors_publication, 'validate_pages', self.gate), mock.patch.object(package, 'build_zip', changing_build):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                package.build(self.root, self.out)
        self.preserved()

    def test_marker_gate_receives_captured_bytes_before_any_publication(self):
        def gate(root, names, captured=None):
            self.assertIsNotNone(captured)
            self.gate(root, names, captured)
            raise ValueError('synthetic honors refusal')
        with mock.patch.object(honors_publication, 'validate_pages', gate), mock.patch.object(package, 'build_zip') as builder:
            with self.assertRaisesRegex(ValueError, 'honors refusal'):
                package.build(self.root, self.out)
            builder.assert_not_called()
        self.preserved()

    def test_manifest_matches_extracted_members_and_preserves_monolith_alias(self):
        with mock.patch.object(honors_publication, 'validate_pages', self.gate):
            result = package.build(self.root, self.out)
        self.assertEqual(result['schema'], 'email-package-v2')
        self.assertEqual(result['status'], 'CURRENT_HASH_BOUND')
        for path, records in zip(self.deliveries[:2], (result['inputs'], result['monolith_inputs'])):
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), [item['arcname'] for item in records])
                for item in records:
                    actual = archive.read(item['arcname'])
                    self.assertEqual(actual, self.original[item['name']])
                    self.assertEqual(item['sha256'], hashlib.sha256(actual).hexdigest())
                    self.assertEqual(item['bytes'], len(actual))
        self.assertEqual(result['monolith_inputs'][0]['arcname'], 'Moneyball.html')
        self.assertEqual(self.deliveries[1].read_bytes(), self.deliveries[2].read_bytes())
        self.assertEqual(json.loads(self.deliveries[3].read_text()), result)
        self.assertFalse(list(self.out.glob('.email-package-*')))

    def test_changed_archive_member_refuses_before_delivery_replacement(self):
        original_build = package.build_zip

        def damaged_build(destination, root, names):
            original_build(destination, root, names)
            if names == package.MONOLITH_FILES:
                with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('Moneyball.html', b'not the captured page')

        with mock.patch.object(honors_publication, 'validate_pages', self.gate), mock.patch.object(package, 'build_zip', damaged_build):
            with self.assertRaisesRegex(RuntimeError, 'differs from captured input'):
                package.build(self.root, self.out)
        self.preserved()


if __name__ == '__main__':
    unittest.main(verbosity=2)
