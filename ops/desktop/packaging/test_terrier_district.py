import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import io
from unittest.mock import patch

from terrier_district import validate_district, validate_github_runtime, main as district_main


class DistrictValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['TERRIER_TEST_SOURCE'])
        sys.path.insert(0, str(cls.source))
        from tools import districts
        cls.definitions = districts

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        for name in ('accounts.sqlite3', 'street.sqlite3', 'scores.sqlite3', 'walk.sqlite3'):
            with closing(sqlite3.connect(self.data / name)) as db:
                db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
                if name == 'street.sqlite3':
                    db.execute('INSERT INTO metadata VALUES (?, ?)', ('source_sha256', json.dumps(self.definitions.get('co-05')['sha256'])))
                db.commit()
        (self.data / '.secret').write_text('fixture-only-preserved-secret')

    def test_exact_existing_district_readonly(self):
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.data.iterdir()}
        self.assertEqual(validate_district(self.source, self.data, 'co-05', legacy=True), {'district': 'co-05', 'validated': True})
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.data.iterdir()})

    def test_wrong_district_and_missing_secret_refused_without_writes(self):
        secret = (self.data / '.secret').read_bytes()
        with self.assertRaises(ValueError):
            validate_district(self.source, self.data, 'or-05', legacy=True)
        self.assertEqual((self.data / '.secret').read_bytes(), secret)
        (self.data / '.secret').unlink()
        with self.assertRaises(ValueError):
            validate_district(self.source, self.data, 'co-05', legacy=True)
        self.assertFalse((self.data / '.secret').exists())

    def test_fresh_missing_directory_is_not_created_by_preflight(self):
        missing = self.data / 'fresh'
        self.assertTrue(validate_district(self.source, missing, 'ct-01')['validated'])
        self.assertFalse(missing.exists())

    def test_corrupt_accounts_refused(self):
        (self.data / 'accounts.sqlite3').write_bytes(b'invalid sqlite fixture')
        with self.assertRaises(sqlite3.DatabaseError):
            validate_district(self.source, self.data, 'co-05', legacy=True)

    def github_cache(self):
        # Deliberately outside the original owner directory.
        temporary = tempfile.TemporaryDirectory(prefix='terrier-github-cache-fixture-')
        self.addCleanup(temporary.cleanup)
        cache = Path(temporary.name)
        source_sha = self.definitions.get('co-05')['sha256']
        for role in ('contacts.sqlite3', 'roads.sqlite3', 'scores.sqlite3', 'street.sqlite3', 'walk.sqlite3'):
            with closing(sqlite3.connect(cache / role)) as connection:
                table = 'meta' if role == 'walk.sqlite3' else 'metadata'
                connection.execute('CREATE TABLE ' + table + ' (key TEXT PRIMARY KEY, value TEXT)')
                key = 'contacts_source_sha256' if role == 'scores.sqlite3' else 'source_sha256'
                connection.execute('INSERT INTO ' + table + ' VALUES (?,?)', (key, source_sha if role == 'walk.sqlite3' else json.dumps(source_sha)))
                connection.commit()
        (cache / 'density.json').write_text('{}')
        return cache

    def test_github_cache_uses_original_owner_state_without_requiring_or_copying_accounts_into_data(self):
        cache = self.github_cache()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.data.iterdir()}
        self.assertEqual(validate_district(self.source, cache, 'co-05', legacy=True,
                                         state_root=self.data, github_data=True), {'district': 'co-05', 'validated': True})
        self.assertFalse((cache / 'accounts.sqlite3').exists())
        self.assertFalse((cache / '.secret').exists())
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.data.iterdir()})
        with self.assertRaises(ValueError):
            validate_district(self.source, cache, 'co-05', legacy=True, state_root=self.data)
        with self.assertRaises(ValueError):
            validate_district(self.source, cache, 'co-05', state_root=self.data, github_data=True)
        with self.assertRaises(ValueError):
            validate_district(self.source, self.data, 'co-05', legacy=True, state_root=self.data, github_data=True)

    def test_github_cache_checks_all_source_bindings_and_original_accounts_integrity(self):
        cache = self.github_cache()
        for role, key in (('contacts.sqlite3', 'source_sha256'), ('walk.sqlite3', 'source_sha256'),
                          ('scores.sqlite3', 'contacts_source_sha256')):
            with self.subTest(role=role):
                original = (cache / role).read_bytes()
                with closing(sqlite3.connect(cache / role)) as connection:
                    table = 'meta' if role == 'walk.sqlite3' else 'metadata'
                    connection.execute('UPDATE ' + table + ' SET value=? WHERE key=?', ('wrong source' if role == 'walk.sqlite3' else json.dumps('wrong source'), key))
                    connection.commit()
                with self.assertRaises(ValueError):
                    validate_github_runtime(self.source, cache, 'co-05')
                (cache / role).write_bytes(original)
        (self.data / 'accounts.sqlite3').write_bytes(b'corrupt original owner account fixture')
        with self.assertRaises(sqlite3.DatabaseError):
            validate_district(self.source, cache, 'co-05', legacy=True, state_root=self.data, github_data=True)

    def test_github_metadata_probe_reads_no_account_or_application_rows(self):
        cache = self.github_cache()
        request = {'sourceRoot': str(self.source), 'dataRoot': str(cache), 'district': 'co-05'}
        incoming, output = type('Input', (), {'buffer': io.BytesIO(json.dumps(request).encode())})(), io.StringIO()
        original, opened = sqlite3.connect, []
        def tracked(database, *args, **kwargs):
            opened.append(str(database))
            return original(database, *args, **kwargs)
        with patch('sys.stdin', incoming), patch('sys.stdout', output), patch('sqlite3.connect', side_effect=tracked):
            self.assertEqual(district_main(github_runtime=True), 0)
        self.assertEqual(json.loads(output.getvalue()), {'district': 'co-05', 'validated': True})
        self.assertTrue(opened)
        self.assertTrue(all('accounts.sqlite3' not in file for file in opened))
        self.assertTrue(all('mode=ro' in file for file in opened))
        self.assertFalse((cache / 'accounts.sqlite3').exists())

    def test_github_probe_cannot_be_used_as_owner_mapping_or_accept_extra_paths(self):
        cache = self.github_cache()
        base = {'sourceRoot': str(self.source), 'dataRoot': str(cache), 'district': 'co-05', 'legacy': True,
                'stateRoot': str(self.data), 'githubDistrictData': True}
        for request, owner, github in ((base, True, False), ({**base, 'legacy': False}, False, False),
                                       ({k: v for k, v in base.items() if k != 'stateRoot'}, False, False),
                                       ({'sourceRoot': str(self.source), 'dataRoot': str(cache), 'district': 'co-05', 'stateRoot': str(self.data)}, False, True)):
            incoming, output = type('Input', (), {'buffer': io.BytesIO(json.dumps(request).encode())})(), io.StringIO()
            with patch('sys.stdin', incoming), patch('sys.stdout', output):
                self.assertEqual(district_main(owner=owner, github_runtime=github), 1)
            self.assertNotIn(str(self.data), output.getvalue())
            self.assertIn('could not be verified', output.getvalue())

    def test_github_runtime_missing_role_sidecar_and_wal_header_are_refused(self):
        cache = self.github_cache()
        database = cache / 'street.sqlite3'
        (cache / 'density.json').unlink()
        with self.assertRaises(ValueError):
            validate_github_runtime(self.source, cache, 'co-05')
        (cache / 'density.json').write_text('{}')
        (cache / 'street.sqlite3-wal').write_bytes(b'fixture')
        with self.assertRaises(ValueError):
            validate_github_runtime(self.source, cache, 'co-05')
        (cache / 'street.sqlite3-wal').unlink()
        raw = database.read_bytes()
        database.write_bytes(raw[:18] + b'\x02\x02' + raw[20:])
        with self.assertRaises(ValueError):
            validate_github_runtime(self.source, cache, 'co-05')

    def test_archive_hashes_are_followed_by_real_installed_source_metadata_validation(self):
        import hashlib
        import zipfile
        from desktop_backend import extract_github_district_archive
        cache = self.github_cache()
        temporary = tempfile.TemporaryDirectory(prefix='terrier-github-extract-fixture-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        archive = root / 'district.zip'
        roles = ('contacts.sqlite3', 'density.json', 'roads.sqlite3', 'scores.sqlite3', 'street.sqlite3', 'walk.sqlite3')
        def request(output):
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
                for role in roles:
                    zipped.write(cache / role, 'co-05/data/' + role)
            return {'archive': str(archive), 'destination': str(output), 'district': 'co-05', 'sourceRoot': str(self.source),
                    'files': [{'role': role, 'path': 'co-05/data/' + role, 'bytes': (cache / role).stat().st_size,
                               'sha256': hashlib.sha256((cache / role).read_bytes()).hexdigest()} for role in roles]}
        result = extract_github_district_archive(request(root / 'accepted'))
        self.assertEqual(result['sourceMetadataAccepted'], True)
        self.assertEqual({p.name for p in (root / 'accepted').iterdir()}, set(roles))
        with closing(sqlite3.connect(cache / 'contacts.sqlite3')) as connection:
            connection.execute('UPDATE metadata SET value=?', (json.dumps('wrong source with valid archive hashes'),))
            connection.commit()
        with self.assertRaises(ValueError):
            extract_github_district_archive(request(root / 'refused'))
        self.assertFalse((root / 'refused').exists())
        self.assertFalse((root / 'accepted' / 'accounts.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
