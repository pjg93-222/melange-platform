"""Selected runtime roots reach the same report lineage and dependency memo.

Synthetic ZIP/JSON/marker files only: no collection, index materialization,
browser, HTTP server, original source database or canonical report is opened.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'app'))
sys.path.insert(0, str(PROJECT / 'tools'))
import contribution_data as data
import graph_data
import source_memo as memo
from report_identity import lineage_paths


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def book(path, source_hash):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="2025-2026" sheetId="1"/></sheets></workbook>')
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="3"><c r="A3" t="inlineStr"><is><t>Source DB SHA-256: ' + source_hash + '</t></is></c></row></sheetData></worksheet>')


class ReportCacheRoots(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.code = self.root / 'code'
        self.code.mkdir()
        self.reports = self.root / 'canonical'
        self.reports.mkdir()
        self.selected = self.root / 'selected-data'
        self.selected.mkdir()
        environment = patch.dict(os.environ, {'MENTAT_DATA_ROOT': '', 'MENTAT_REPORT_CACHE_ROOT': '',
            'MENTAT_RECEIPT_ROOTS': '', 'MENTAT_NATIVE': '0', 'MENTAT_DRAWER_SIDECARS': '0'})
        environment.start()
        self.addCleanup(environment.stop)
        project = patch.object(data, 'PROJECT', self.code)
        project.start()
        self.addCleanup(project.stop)
        memo.uninstall()
        self.addCleanup(memo.uninstall)
        self.database = self.selected / 'original.db'
        self.database.write_bytes(b'only a synthetic source marker; never opened as sqlite')
        self.source_hash = data._hash(self.database)
        self.workbook = self.reports / 'Fixture Industry Spending Overview.xlsx'
        book(self.workbook, self.source_hash)

    def lineage(self, cache, database=None, expected=12.34):
        manifest, payload = lineage_paths(cache / 'fixture', self.source_hash)
        write_json(payload, {'metadata': {'industry': 'Fixture',
            'database_path': str(database or self.database), 'expected_cycle_totals': {'2025-2026': expected}}})
        write_json(manifest, {'database_sha256': self.source_hash, 'data_sha256': data._hash(payload)})
        return manifest, payload

    def test_explicit_report_cache_override_wins_over_data_default(self):
        os.environ['MENTAT_DATA_ROOT'] = str(self.selected)
        chosen = self.root / 'separate-report-cache'
        os.environ['MENTAT_REPORT_CACHE_ROOT'] = str(chosen)
        manifest, payload = self.lineage(chosen)
        source = data._sources(self.reports)[0]
        self.assertTrue(source['available'])
        self.assertEqual(source['manifest'], manifest)
        self.assertEqual(source['payload'], payload)
        self.assertFalse((self.code / '.cache').exists())

    def test_data_root_supplies_default_report_cache(self):
        os.environ['MENTAT_DATA_ROOT'] = str(self.selected)
        manifest, _ = self.lineage(self.selected / '.cache/industry-report')
        source = data._sources(self.reports)[0]
        self.assertTrue(source['available'])
        self.assertEqual(source['manifest'], manifest)

    def test_project_injection_remains_the_no_override_default(self):
        manifest, _ = self.lineage(self.code / '.cache/industry-report')
        self.assertEqual(data._sources(self.reports)[0]['manifest'], manifest)

    def test_memo_root_switch_invalidates_unavailable_and_available_results(self):
        empty = self.root / 'absent-cache'
        first = self.root / 'first-cache'
        second = self.root / 'second-cache'
        self.lineage(first, expected=12.34)
        self.lineage(second, expected=99.01)
        memo.install()
        os.environ['MENTAT_REPORT_CACHE_ROOT'] = str(empty)
        self.assertFalse(data._sources(self.reports)[0]['available'])
        os.environ['MENTAT_REPORT_CACHE_ROOT'] = str(first)
        source = data._sources(self.reports)[0]
        self.assertTrue(source['available'])
        self.assertEqual(source['expected_cycle_totals'], {'2025-2026': 12.34})
        os.environ['MENTAT_REPORT_CACHE_ROOT'] = str(second)
        self.assertEqual(data._sources(self.reports)[0]['expected_cycle_totals'], {'2025-2026': 99.01})

    def test_hydration_is_exact_and_contained_in_selected_data_cache(self):
        os.environ['MENTAT_DATA_ROOT'] = str(self.selected)
        cache = self.selected / '.cache'
        self.lineage(cache / 'industry-report', database=self.selected / 'missing-original.db')
        recovered = cache / 'receipt-metadata-repair-20260910/fixture/mentat.db'
        recovered.parent.mkdir(parents=True)
        recovered.write_bytes(b'synthetic preserved recovery')
        receipt = recovered.with_name('mentat.db.hydration.json')
        write_json(receipt, {'source_sha256': self.source_hash, 'output': str(recovered)})
        memo.install()
        source = data._sources(self.reports)[0]
        self.assertTrue(source['available'])
        self.assertEqual(source['database'], recovered.resolve())
        outside = self.root / 'outside.db'
        outside.write_bytes(b'not inside the selected cache')
        write_json(receipt, {'source_sha256': self.source_hash, 'output': str(outside)})
        refused = data._sources(self.reports)[0]
        self.assertFalse(refused['available'])
        self.assertIn('pinned recovered contribution database is unavailable', refused['message'])

    def test_graph_full_transfer_and_audited_store_paths_use_selected_cache(self):
        chosen = self.root / 'graph-report-cache'
        os.environ['MENTAT_REPORT_CACHE_ROOT'] = str(chosen)
        payload, manifest = graph_data.transfer_source(self.workbook)
        self.assertEqual(payload.parent, chosen / 'fixture')
        self.assertEqual(manifest.parent, chosen / 'fixture')
        cycle_book = self.reports / 'Fixture Industry Spending Overview 2025-2026.xlsx'
        book(cycle_book, self.source_hash)
        store, cycle_manifest = graph_data.audited_store(cycle_book, '2025-2026')
        self.assertEqual(store.parent, chosen / 'fixture')
        self.assertEqual(cycle_manifest.parent, chosen / 'fixture')
        self.assertIn('20252026', cycle_manifest.name)


if __name__ == '__main__':
    unittest.main()
