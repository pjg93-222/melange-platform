"""Real cycle workbooks/payloads: isolation, lineage rejection, and memo refresh."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'app'))
sys.path.insert(0, str(PROJECT / 'tools'))
import contribution_data as data
import snapshot_registry
import source_memo
import industry_scope
from report_identity import identity, lineage_paths
from publish_snapshot_lineage import publish


class CycleSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reports = self.root / 'reports'
        self.reports.mkdir()
        self.database = self.root / 'source.db'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE marker(value)')
        self.db_hash = data._hash(self.database)
        (self.root / 'tools').mkdir()
        (self.root / 'tools/cross_reference_engine.py').write_bytes((PROJECT / 'tools/cross_reference_engine.py').read_bytes())
        self.addCleanup(source_memo.uninstall)
        self.patch = patch.object(data, 'PROJECT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def fixture(self, cycle='2023-2024', amount=123):
        name = f'Fixture Industry Spending Overview {cycle}.xlsx'
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = 'Spending Overview'
        sheet.append(['Source DB SHA-256: ' + self.db_hash])
        for _ in range(3):
            sheet.append([])
        sheet.append(['Cycle', 'Scope', 'Total Giving'])
        sheet.append([cycle, 'Total Giving', amount])
        workbook = self.reports / name
        book.save(workbook)
        book.close()
        payload = self.root / ('input-' + cycle + '.json')
        payload.write_text(json.dumps({'metadata': {'industry': 'Fixture', 'database_path': str(self.database),
                                                 'expected_cycle_totals': {cycle: amount}},
                                      'raw_rows': []}), encoding='utf-8')
        receipt = publish(workbook, payload, project=self.root, reports=self.reports)
        return workbook, payload, receipt

    def sources(self):
        return source_memo._original(self.reports)

    def test_two_cycles_have_separate_registry_and_catalog_identities(self):
        self.fixture()
        self.fixture('2021-2022', 456)
        sources = self.sources()
        self.assertEqual([s['industry'] for s in sources], ['Fixture', 'Fixture'])
        self.assertEqual([s['id'] for s in sources], ['Fixture 2021-2022', 'Fixture 2023-2024'])
        self.assertTrue(all(s['available'] for s in sources))
        registry = snapshot_registry.registry(self.reports, sources)
        self.assertEqual(len({s['build_stamp'] for s in registry['snapshots']}), 2)
        catalog = data.catalog(self.reports, sources)
        for item in catalog['datasets']:
            self.assertEqual(industry_scope.resolve(registry, item['industry'], item['id'])['dataset'], item['id'])

    def test_bad_payload_hash_and_wrong_cycle_are_unavailable(self):
        _, _, receipt = self.fixture()
        path = Path(receipt['manifest'])
        manifest = json.loads(path.read_text())
        for key, bad in [('data_sha256', '0' * 64), ('cycle_label', '2021-2022')]:
            path.write_text(json.dumps({**manifest, key: bad}))
            source = self.sources()[0]
            self.assertFalse(source['available'])
            self.assertIn('Cycle-specific', source['message'])
        path.write_text(json.dumps(manifest))
        self.assertTrue(self.sources()[0]['available'])

    def test_missing_cycle_lineage_cannot_fall_back_to_all_cycles(self):
        _, _, receipt = self.fixture()
        path = Path(receipt['manifest'])
        path.rename(path.with_name('manifest-' + self.db_hash[:16] + '.json'))
        self.assertFalse(self.sources()[0]['available'])

    def test_memo_tracks_cycle_payload_replacement(self):
        _, _, receipt = self.fixture()
        self.assertTrue(source_memo.sources(self.reports)[0]['available'])
        Path(receipt['payload']).write_text('{"metadata": {}}')
        self.assertFalse(source_memo.sources(self.reports)[0]['available'])

    def test_publisher_refuses_changed_database_or_payload_total(self):
        workbook, payload, _ = self.fixture()
        body = json.loads(payload.read_text())
        body['metadata']['expected_cycle_totals']['2023-2024'] = 999
        payload.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, 'totals disagree'):
            publish(workbook, payload, project=self.root, reports=self.reports)
        body['metadata']['expected_cycle_totals']['2023-2024'] = 123
        payload.write_text(json.dumps(body))
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO marker VALUES (1)')
        with self.assertRaisesRegex(ValueError, 'source fingerprint'):
            publish(workbook, payload, project=self.root, reports=self.reports)

    def test_publisher_refuses_replaced_workbook_for_existing_lineage(self):
        workbook, payload, receipt = self.fixture()
        book = openpyxl.load_workbook(workbook)
        book.active['B1'] = 'Edited after publication'
        book.save(workbook)
        book.close()
        with self.assertRaisesRegex(ValueError, 'different manifest already owns'):
            publish(workbook, payload, project=self.root, reports=self.reports)
        source = self.sources()[0]
        self.assertFalse(source['available'])
        self.assertIn('Cycle-specific', source['message'])
        self.assertEqual(Path(receipt['manifest']).is_file(), True)

    def test_names_reject_partial_or_invalid_cycle_suffixes(self):
        for name in ['Fixture Industry Spending Overview 2024-2025.xlsx',
                     'Fixture Industry Spending Overview 2022-2024.xlsx',
                     'Fixture Industry Spending Overview 2023.xlsx']:
            self.assertIsNone(identity(name))
        self.assertEqual(identity('Fixture Industry Spending Data 2023-2024.xlsx')['kind'], 'data')


if __name__ == '__main__':
    unittest.main()
