"""One scan of the report folder per contribution request (perf lane 2026-10-08).

A verified POST /api/contributions/query scanned the folder three times - scope_body's registry,
verify_sources, then the reader's own _source - and each scan reads every industry's payload manifest and
the 2 MB head of its payload. The request now scans once and hands that list to all three, and the manifest
and payload head are read once per file state. The guard a second scan used to give (the report replaced
between verification and the answer) is kept by hashing the file again.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP.parent / 'tools'))
import contribution_data as data  # noqa: E402
import source_memo as memo  # noqa: E402
from test_industry_routes import RouteBase, app  # noqa: E402
from test_source_memo import Snapshots, changed, A, B, C  # noqa: E402


class OneScanPerRequest(RouteBase):
    def setUp(self):
        super().setUp()  # _sources is a recording double (self.scans); verify_sources answers []

        def verify(report_directory, dataset=None, strict=True, sources=None):
            # What the real verify_sources does first: scan, unless the request already did.
            if sources is None:
                app.contribution_data._sources(report_directory)
            return []
        for target, attribute, value in (
                (app.contribution_workbook_guard, 'verify_sources', verify),
                (app.contribution_data, '_ready', lambda source, retry=False: (None, {'status': 'preparing', 'retry_after_ms': 1500}))):
            fake = patch.object(target, attribute, value)
            fake.start()
            self.addCleanup(fake.stop)

    def scans_for(self, *call, **kwargs):
        self.scans = []
        status, body = self.request(*call, **kwargs)
        return status, body, len(self.scans)

    def test_a_verified_query_scans_the_folder_once(self):
        request = {'industry': 'Banking', 'basis': 'original_observed', 'dimensions': ['zip5'], 'measures': ['amount']}
        for route in ('/api/contributions/query', '/api/contributions/records'):
            with self.subTest(route=route):
                status, body, scans = self.scans_for(route, method='POST', data=request)
                self.assertEqual((status, body['status'], body['industry']), (200, 'preparing', 'Banking'), body)
                self.assertEqual(scans, 1)
        # The same with the dataset named instead of the industry.
        by_dataset = {key: value for key, value in {**request, 'dataset': 'Banking'}.items() if key != 'industry'}
        status, body, scans = self.scans_for('/api/contributions/query', method='POST', data=by_dataset)
        self.assertEqual((status, scans), (200, 1), body)

    def test_the_catalogs_scan_the_folder_once(self):
        for route in ('/api/contributions/catalog', '/api/contributions/catalog?industry=Banking', '/api/insights/catalog'):
            with self.subTest(route=route):
                status, body, scans = self.scans_for(route)
                self.assertEqual(status, 200, body)
                self.assertEqual(scans, 1)


class ReplacedBetweenGuardAndAnswer(RouteBase):
    """One scan serves the guard and the reader, so their hashes agree by construction: the file is hashed
    again after the answer, and a report replaced meanwhile is refused as before."""

    def setUp(self):
        super().setUp()
        self.item = dict(self.snapshots[0])  # Banking
        self.item['workbook_sha256'] = data._hash(self.item['workbook'])
        self.snapshots = [self.item]
        pinned = [{'dataset': 'Banking', 'matched': True, 'workbook_sha256': self.item['workbook_sha256'],
                   'database_sha256': None}]
        for target, attribute, value in (
                (app.contribution_workbook_guard, 'verify_sources', lambda *_a, **_k: pinned),
                (app.contribution_query, 'query', self.reader)):
            fake = patch.object(target, attribute, value)
            fake.start()
            self.addCleanup(fake.stop)
        self.replace = False

    def reader(self, report_directory, request, sources=None):
        if self.replace:
            path = self.item['workbook']
            path.write_bytes(path.read_bytes() + b' replaced')
        return {'status': 'ready', 'rows': [], 'provenance': {'workbook_sha256': self.item['workbook_sha256']}}

    def query(self):
        return self.request('/api/contributions/query', method='POST',
                            data={'industry': 'Banking', 'basis': 'original_observed', 'dimensions': ['zip5'],
                                  'measures': ['amount']})

    def test_an_unchanged_report_is_answered(self):
        status, body = self.query()
        self.assertEqual((status, body['status']), (200, 'ready'), body)

    def test_a_report_replaced_after_the_scan_is_refused(self):
        self.replace = True
        status, body = self.query()
        self.assertEqual(status, 400)
        self.assertIn('source changed', body['error'])


class StampedReads(unittest.TestCase):
    """The manifest and the payload head are read once per file state; the glob still runs every scan."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.snapshots = Snapshots(self.tmp.name)
        self.snapshots.add('Alpha', A)
        self.snapshots.add('Beta', B, hydrated=True)
        project = patch.object(data, 'PROJECT', self.snapshots.project)
        project.start()
        self.addCleanup(project.stop)
        data._workbook_source.cache_clear()
        self.addCleanup(data._workbook_source.cache_clear)
        self.reports = self.snapshots.reports
        self.manifests = [self.snapshots.cache('Alpha') / f'manifest-{A[:16]}.json',
                          self.snapshots.cache('Beta') / f'manifest-{B[:16]}.json']
        self.payloads = [self.snapshots.cache('Alpha') / f'industry_data-{A[:16]}.json',
                         self.snapshots.cache('Beta') / f'industry_data-{B[:16]}.json']

    def scan(self):
        """(sources, how many times each lineage file was opened) for one unmemoized scan."""
        real, opened = Path.open, []

        def spy(path, *args, **kwargs):
            opened.append(path.resolve())
            return real(path, *args, **kwargs)
        with patch.object(Path, 'open', spy):
            sources = memo._original(self.reports)
        files = [path.resolve() for path in self.manifests + self.payloads]
        return sources, {path.name: opened.count(path) for path in files}

    def test_a_second_scan_does_not_reread_what_did_not_change(self):
        first, reads = self.scan()
        self.assertEqual([s['available'] for s in first], [True, True])
        self.assertEqual(set(reads.values()), {1})
        second, reads = self.scan()
        self.assertEqual(set(reads.values()), {0})
        self.assertEqual(second, first)

    def test_an_edited_file_is_read_again_and_an_added_workbook_is_seen(self):
        self.scan()
        changed(self.payloads[0], lambda: self.payloads[0].write_text(
            self.payloads[0].read_text().replace('12.5', '99.5'), encoding='utf-8'))
        sources, reads = self.scan()
        self.assertEqual(sources[0]['expected_cycle_totals'], {'2023-2024': 99.5})
        self.assertEqual({name: count for name, count in reads.items() if count}, {self.payloads[0].name: 1})
        self.snapshots.add('Gamma', C)
        sources, _ = self.scan()
        self.assertEqual([s['id'] for s in sources], ['Alpha', 'Beta', 'Gamma'])

    def test_a_caller_cannot_change_what_the_next_scan_reads(self):
        first, _ = self.scan()
        first[0]['expected_cycle_totals']['2023-2024'] = -1
        second, _ = self.scan()
        self.assertEqual(second[0]['expected_cycle_totals'], {'2023-2024': 12.5})


if __name__ == '__main__':
    unittest.main()
