"""Temporary-only repro: compile actual function bodies, never execute producer CLIs."""
import ast
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(os.environ.get('FEC_SURFACE_SOURCE_ROOT', Path(__file__).resolve().parents[3]))


def functions(name, wanted, namespace):
    path = ROOT / 'fec/surfaces' / name
    tree = ast.parse(path.read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted]
    assert len(selected) == len(wanted)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


class BytePreservationTests(unittest.TestCase):
    def test_lock_receipt_and_decryption_retain_original_bytes(self):
        namespace = functions('lock_dashboard.py', {'lock_dashboard'}, {
            'os': os, 'base64': base64, 'hashlib': hashlib, 'AESGCM': AESGCM, 'ITERS': 1000,
            'WRAP': '__SALT__|__IV__|__CT__|__ITERS__'})
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'source.html', Path(directory) / 'locked.html'
            for original in (b'<!DOCTYPE html>\r\n<html>fixture</html>\r\n', b'<html>fixture</html>\n',
                             b'\xef\xbb\xbf<html>\r\nfixture\n</html>\r\n'):
                with self.subTest(original=original):
                    source.write_bytes(original)
                    receipt = namespace['lock_dashboard'](source, output, 'fixture-password')
                    salt, iv, ciphertext, iterations = output.read_text().split('|')
                    key = hashlib.pbkdf2_hmac('sha256', b'fixture-password', base64.b64decode(salt), int(iterations), dklen=32)
                    decrypted = AESGCM(key).decrypt(base64.b64decode(iv), base64.b64decode(ciphertext), None)
                    self.assertEqual((receipt['source_sha256'], decrypted), (hashlib.sha256(original).hexdigest(), original))
                    self.assertEqual(receipt['output_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())

    def test_lock_invalid_utf8_or_empty_password_preserves_previous_output(self):
        namespace = functions('lock_dashboard.py', {'lock_dashboard'}, {
            'os': os, 'base64': base64, 'hashlib': hashlib, 'AESGCM': AESGCM, 'ITERS': 1000,
            'WRAP': '__SALT__|__IV__|__CT__|__ITERS__'})
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'source.html', Path(directory) / 'locked.html'
            source.write_bytes(b'\xffinvalid utf8')
            output.write_bytes(b'previous final')
            with self.assertRaises(UnicodeDecodeError):
                namespace['lock_dashboard'](source, output, 'fixture-password')
            with self.assertRaisesRegex(RuntimeError, 'password is required'):
                namespace['lock_dashboard'](source, output, '')
            self.assertEqual(output.read_bytes(), b'previous final')
            self.assertFalse(list(Path(directory).glob('*.tmp')))

    def test_retired_navigation_install_retains_original_pages(self):
        namespace = functions('install_staffer_live.py', {'sha', 'install'}, {
            'Path': Path, 'hashlib': hashlib, 'os': os, 'json': json,
            'datetime': datetime, 'timezone': timezone, 'FILES': (), 'inject_navigation': lambda text: text})
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'target'
            source.mkdir()
            builders = target / 'fec/surfaces'
            builders.mkdir(parents=True)
            existing_hook = b'from surfaces.staffer_live_integration import inject_navigation\r\n'
            for name in ('make_dashboard_html.py', 'make_combined.py'):
                (builders / name).write_bytes(existing_hook)
            original = b'<!doctype html>\r\n<html>unchanged fixture</html>\r\n'
            pages = [target / name for name in ('Moneyball.html', 'Moneyball Dashboard.html')]
            for page in pages:
                page.write_bytes(original)
            receipt = namespace['install'](source, target)
            self.assertEqual([page.read_bytes() for page in pages], [original, original])
            self.assertTrue(all(not row['changed'] for row in receipt['changes']))

    def test_surgical_builder_hooks_keep_original_line_endings(self):
        namespace = functions('install_staffer_live.py', {'sha', 'install'}, {
            'Path': Path, 'hashlib': hashlib, 'os': os, 'json': json,
            'datetime': datetime, 'timezone': timezone, 'FILES': (), 'inject_navigation': lambda text: text})
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=repr(newline)), tempfile.TemporaryDirectory() as directory:
                source, target = Path(directory) / 'source', Path(directory) / 'target'
                source.mkdir()
                builders = target / 'fec/surfaces'
                builders.mkdir(parents=True)
                prefix, suffix = '# unrelated existing prefix\r\n', '# unrelated tail\n'
                dashboard_line = "    html=_tpl('make_dashboard_html_00.tpl')"
                combined_line = "out = os.path.join(B, 'Moneyball.html')"
                dashboard = builders / 'make_dashboard_html.py'
                combined = builders / 'make_combined.py'
                dashboard.write_bytes((prefix + dashboard_line + newline + suffix).encode())
                combined.write_bytes((prefix + combined_line + newline + suffix).encode())
                previous = {path: path.read_bytes() for path in (dashboard, combined)}
                namespace['install'](source, target)
                self.assertEqual(dashboard.read_bytes(), (prefix + '    from surfaces.staffer_live_integration import inject_navigation' + newline + "    html=inject_navigation(_tpl('make_dashboard_html_00.tpl'))" + newline + suffix).encode())
                self.assertEqual(combined.read_bytes(), (prefix + 'from surfaces.staffer_live_integration import inject_navigation' + newline + 'HTML = inject_navigation(HTML)' + newline + combined_line + newline + suffix).encode())
                for path, before in previous.items():
                    backup = target / 'audits/staffer_live_runtime/backups' / path.relative_to(target)
                    self.assertEqual(backup.read_bytes(), before)
                # A repeated install must leave both hooks byte-identical.
                installed = {path: path.read_bytes() for path in (dashboard, combined)}
                again = namespace['install'](source, target)
                self.assertEqual(installed, {path: path.read_bytes() for path in installed})
                self.assertEqual(again['changes'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
