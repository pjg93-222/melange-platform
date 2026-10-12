"""refresh_fec_sources can hand preserved filing headers to the transfer index.

Nothing here downloads, builds an index or touches a real snapshot: main() runs
with its downloader, its receipt writer and both transfer-graph entry points
replaced, so only its argument handling is exercised.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))

import fec_transfer_graph
import refresh_fec_sources


class FilingHeaderArgumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / 'sources'
        self.headers = self.root / 'headers'
        self.headers.mkdir(parents=True)
        self.first = {'header': {'file_num': '1001', 'filing_form': 'F3X'}}
        self.second = {'header': {'file_num': '1002', 'filing_form': 'F3X'}}
        self.rejected = {'header': {'file_num': '1003', 'filing_form': 'F3X'}}
        # Written out of order on purpose: the loader sorts by path.
        (self.headers / 'b.json').write_text(json.dumps(self.second), encoding='utf-8')
        (self.headers / 'a.json').write_text(json.dumps(self.first), encoding='utf-8')
        (self.headers / 'c.review.json').write_text(json.dumps(self.rejected), encoding='utf-8')

    def run_main(self, *extra, cycles=('2024',)):
        calls = []

        def fake_index(path, cycle, filing_headers=()):
            calls.append({'path': Path(path), 'cycle': cycle, 'headers': filing_headers})
            return {'edges': [], 'counts': {}, 'built_seconds': 0.0}

        argv = ['refresh_fec_sources.py', '--root', str(self.sources),
                '--families', 'oth', '--cycles', *cycles, *extra]
        with patch.object(sys, 'argv', argv), \
             patch.object(refresh_fec_sources, 'refresh',
                          lambda root, cycle, family: {'cycle': cycle, 'family': family}), \
             patch.object(refresh_fec_sources, 'write_receipt', lambda path, value: None), \
             patch.object(fec_transfer_graph, 'transfer_index', fake_index), \
             patch.object(fec_transfer_graph, 'publish_index', lambda directory, index: None):
            refresh_fec_sources.main()
        return calls

    def test_headers_are_loaded_in_path_order_and_review_files_skipped(self):
        calls = self.run_main('--filing-header-directory', str(self.headers))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['headers'], [self.first, self.second])
        self.assertNotIn(self.rejected, calls[0]['headers'])

    def test_every_cycle_receives_the_same_headers(self):
        calls = self.run_main('--filing-header-directory', str(self.headers),
                              cycles=('2022', '2024', '2026'))
        self.assertEqual([call['cycle'] for call in calls], [2022, 2024, 2026])
        for call in calls:
            self.assertEqual(call['headers'], [self.first, self.second])
            self.assertEqual(call['path'].name, 'itoth.txt')
        self.assertEqual({call['path'].parent.name for call in calls},
                         {'oth22', 'oth24', 'oth26'})

    def test_absent_argument_passes_no_headers(self):
        calls = self.run_main()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['headers'], [])

    def test_empty_header_list_is_the_same_call_as_the_omitted_argument(self):
        # transfer_index() normalises its argument with list() on its first line,
        # so the no-argument path is byte-identical to today's two-positional
        # call: the verified-header map and the cache-key digest both match.
        signature = inspect.signature(fec_transfer_graph.transfer_index)
        self.assertEqual(signature.parameters['filing_headers'].default, ())
        self.assertEqual(fec_transfer_graph.verified_headers(list(())),
                         fec_transfer_graph.verified_headers([]))
        self.assertEqual(
            hashlib.sha256(json.dumps(list(()), sort_keys=True).encode()).hexdigest(),
            hashlib.sha256(json.dumps([], sort_keys=True).encode()).hexdigest())

    def test_a_missing_header_directory_yields_no_headers_like_its_twin(self):
        # Path.glob() on an absent directory is empty rather than an error, so a
        # mistyped path resolves nothing instead of failing. That is exactly what
        # fec_transfer_graph.main() already does; the loaders stay identical.
        calls = self.run_main('--filing-header-directory', str(self.root / 'absent'))
        self.assertEqual(calls[0]['headers'], [])
        self.assertFalse(list((self.root / 'absent').glob('*.json')))

    def test_loader_is_the_twin_of_the_transfer_graph_loader(self):
        def loader(text):
            start = text.index("[json.loads(p.read_text(encoding='utf-8'))")
            end = text.index('else []', start) + len('else []')
            return ' '.join(text[start:end].split())

        refresher = (TOOLS / 'refresh_fec_sources.py').read_text(encoding='utf-8')
        twin = (TOOLS / 'fec_transfer_graph.py').read_text(encoding='utf-8')
        self.assertEqual(loader(refresher), loader(twin))

    def test_the_flag_matches_the_transfer_graph_flag(self):
        for text in ((TOOLS / 'refresh_fec_sources.py').read_text(encoding='utf-8'),
                     (TOOLS / 'fec_transfer_graph.py').read_text(encoding='utf-8')):
            self.assertIn("parser.add_argument('--filing-header-directory',type=Path,", text)


if __name__ == '__main__':
    unittest.main()
