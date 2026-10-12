"""PAC Flow selection stays independent of saved evidence snapshots."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import pac_flow_access  # noqa: E402


class Catalog(unittest.TestCase):
    def test_sector_lists_its_pacs_across_reports_without_duplicates(self):
        class App:
            def graph_catalog(self, _run):
                return {'industries': [{'name': 'Crypto', 'indices': [1, 2]}]}

            def report_path(self, _run, index):
                return Path(str(index))

        def report(stamp):
            rows = [{'recipient_id': 'C00835959', 'recipient': 'FAIRSHAKE',
                     'category': 'Super PAC', 'cycle': '2024'}]
            if str(stamp[0]) == '2':
                rows += [{'recipient_id': 'C00835959', 'recipient': 'FAIRSHAKE',
                          'category': 'Super PAC', 'cycle': '2026'},
                         {'recipient_id': 'C00000001', 'recipient': 'A candidate',
                          'category': 'Candidate', 'cycle': '2024'}]
            return {'facts': rows}

        with patch.object(pac_flow_access.graph_data, 'stamp', side_effect=lambda path: (str(path),)), \
             patch.object(pac_flow_access.graph_data, 'report_facts', side_effect=report):
            found = pac_flow_access.catalog(App(), 'Crypto')
        self.assertEqual(found['total'], 1)
        self.assertEqual(found['committees'][0]['id'], 'C00835959')
        self.assertEqual(found['committees'][0]['cycles'], ['2026', '2024'])

    def test_direct_read_only_never_starts_a_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'read-only'):
                pac_flow_access.direct_evidence('C00835959', '2024', directory, allow_collect=False)
            self.assertFalse((Path(directory) / 'direct-c00835959-2024').exists())

    def test_exact_fec_id_can_open_even_if_directory_has_not_listed_it(self):
        class App:
            def graph_catalog(self, _run):
                return {'industries': []}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'fec_committee_master'
            root.mkdir()
            with patch.object(pac_flow_access.evidence_csv, 'INPUTS', Path(directory)):
                found = pac_flow_access.catalog(App(), query='C99999999')
        self.assertEqual(found['committees'][0]['id'], 'C99999999')

    def test_global_fec_id_still_works_if_report_folder_is_unavailable(self):
        class App:
            def graph_catalog(self, _run):
                raise ValueError('Report folder unavailable')

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'fec_committee_master').mkdir()
            with patch.object(pac_flow_access.evidence_csv, 'INPUTS', Path(directory)):
                found = pac_flow_access.catalog(App(), query='C99999999')
        self.assertEqual(found['committees'][0]['id'], 'C99999999')


if __name__ == '__main__':
    unittest.main()
