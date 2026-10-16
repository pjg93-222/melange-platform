import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from terrier_district import validate_district


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


if __name__ == '__main__':
    unittest.main()
