"""Temporary files and an AST-lifted commit method; no runner import or launch."""
import ast
import datetime
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

FEC = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('refresh_hash_fixture', FEC / 'refresh_state.py')
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)


class ReachedValidCommit(Exception):
    pass


class HashRefusal(unittest.TestCase):
    def test_manifest_without_digest_is_not_verified(self):
        with tempfile.TemporaryDirectory(prefix='fec-refresh-hash-') as temporary:
            root = Path(temporary)
            (root / 'fixture.csv').write_bytes(b'header\nvalue\n')
            for digest in (None, ''):
                with self.subTest(digest=digest):
                    self.assertEqual(S.verify_manifest(root, {'files': {'fixture.csv': {'sha256': digest}}}), ['fixture.csv'])
            digest = S.sha256_file(root / 'fixture.csv')
            self.assertEqual(S.verify_manifest(root, {'files': {'fixture.csv': {'sha256': digest}}}), [])
            (root / 'fixture.csv').write_bytes(b'changed')
            self.assertEqual(S.verify_manifest(root, {'files': {'fixture.csv': {'sha256': digest}}}), ['fixture.csv'])

    def commit_fixture(self, root, missing_hash):
        source = FEC / 'refresh_tick.py'
        parsed = ast.parse(source.read_bytes(), filename=str(source))
        function = next(n for n in ast.walk(parsed) if isinstance(n, ast.FunctionDef) and n.name == '_commit_step')
        namespace = dict(state_store=S, table_module=SimpleNamespace(), COVERAGE_OK=S.COVERAGE_OK,
                         PARTIAL=S.PARTIAL, OK=S.OK)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        failures = []
        owner = SimpleNamespace(root=str(root), manifests={}, owner={}, accept_zero=set(), review_added=0,
                                manifest_dirty=set(), state=S.RefreshState(root / 'state.json'),
                                now=lambda: datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
                                manifest_path=lambda name, digest: str(root / 'manifest.json'))
        def fail(*args):
            failures.append(args)
            return False
        owner._fail_step = fail
        step = SimpleNamespace(name='fixture collector', slug='fixture-collector')
        run = SimpleNamespace(returncode=0)
        file = root / 'fixture.csv'
        file.write_bytes(b'header\nvalue\n')
        digest = None if missing_hash else S.sha256_file(file)
        with mock.patch.object(S, 'sha256_file', return_value=digest), mock.patch.object(
                S, 'write_json_atomic', side_effect=ReachedValidCommit('would commit manifest')):
            if missing_hash:
                self.assertFalse(namespace['_commit_step'](owner, step, {}, {'fixture.csv': (13, 1)}, S.OK, 1, run))
                self.assertEqual(len(failures), 1)
                self.assertEqual(owner.state.step(step.name)['unverified_files'], ['fixture.csv'])
                self.assertEqual(failures[0][3], S.TORN)
                self.assertIn('fixture.csv', failures[0][5])
            else:
                with self.assertRaises(ReachedValidCommit):
                    namespace['_commit_step'](owner, step, {}, {'fixture.csv': (13, 1)}, S.OK, 1, run)
                self.assertEqual(failures, [])

    def test_unreadable_hash_refuses_before_manifest_write(self):
        with tempfile.TemporaryDirectory(prefix='fec-refresh-refusal-') as temporary:
            self.commit_fixture(Path(temporary), True)

    def test_readable_hash_keeps_existing_commit_entry(self):
        with tempfile.TemporaryDirectory(prefix='fec-refresh-valid-') as temporary:
            self.commit_fixture(Path(temporary), False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
