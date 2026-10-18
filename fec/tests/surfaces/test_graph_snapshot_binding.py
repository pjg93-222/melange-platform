"""Receipt-only fixtures; no real graph or index read and no producer invocation."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(os.environ.get('FEC_SURFACE_SOURCE_ROOT', Path(__file__).resolve().parents[3]))
spec = importlib.util.spec_from_file_location('snapshot_binding_fixture', ROOT / 'fec/surfaces/staffer_graph_snapshot.py')
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


class SnapshotBindingTests(unittest.TestCase):
    def fixture(self, directory, change):
        audits = Path(directory) / 'audits/staffer_network'
        generation = audits / snapshot.GENERATION
        verifier = audits / snapshot.VERIFICATION
        generation.mkdir(parents=True)
        verifier.mkdir()
        raw = b'{"files":[],"inputs":[]}'
        (generation / 'staffer_social_network_receipt.json').write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        row = {'path': 'staffer_social_network_receipt.json', 'primary_sha256': digest,
               'twin_sha256': digest, 'identical': True}
        receipt = {'status': 'PASS', 'failures': [], 'twin_byte_identity': [row]}
        change(receipt)
        (verifier / 'staffer_social_network_verification_receipt.json').write_text(json.dumps(receipt))
        return snapshot.StaticGraph(directory)

    def test_missing_row_refuses_once_and_records_failure_for_repeated_view(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = self.fixture(directory, lambda receipt: receipt.update(twin_byte_identity=[]))
            graph.load()
            self.assertTrue(graph.errors)
            self.assertFalse(graph.view()['bounds']['derived_layer_count_parity'])

    def test_mismatching_twin_cannot_admit_generation_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = self.fixture(directory, lambda receipt: receipt['twin_byte_identity'][0].update(twin_sha256='0' * 64, identical=False))
            with mock.patch.object(graph, '_verified', wraps=graph._verified) as verified:
                graph.load()
            self.assertEqual(verified.call_count, 0)
            self.assertTrue(graph.errors)

    def test_duplicate_receipt_binding_cannot_admit_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = self.fixture(directory, lambda receipt: receipt['twin_byte_identity'].append(dict(receipt['twin_byte_identity'][0])))
            with mock.patch.object(graph, '_verified', wraps=graph._verified) as verified:
                graph.load()
            self.assertEqual(verified.call_count, 0)
            self.assertTrue(graph.errors)

    def test_malformed_rows_fail_closed_without_reading_generation(self):
        for value in (None, {}, '', [None], ['bad'], [42]):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                graph = self.fixture(directory, lambda receipt: receipt.update(twin_byte_identity=value))
                with mock.patch.object(graph, '_verified', wraps=graph._verified) as verified:
                    graph.load()
                self.assertEqual(verified.call_count, 0)
                self.assertTrue(graph.errors)
                self.assertEqual(graph.nodes, {})

    def test_invalid_hash_or_identical_flag_refuses_before_admission(self):
        changes = ({'primary_sha256': None}, {'primary_sha256': ''}, {'primary_sha256': 'g' * 64},
                   {'twin_sha256': None}, {'identical': 1}, {'identical': False})
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                graph = self.fixture(directory, lambda receipt: receipt['twin_byte_identity'][0].update(change))
                with mock.patch.object(graph, '_verified', wraps=graph._verified) as verified:
                    graph.load()
                self.assertEqual(verified.call_count, 0)
                self.assertTrue(graph.errors)

    def test_valid_binding_admits_unchanged_receipt_before_artifact_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = self.fixture(directory, lambda receipt: None)
            paths = list(Path(directory).rglob('*.json'))
            before = {path: path.read_bytes() for path in paths}
            graph.load()
            self.assertEqual([row['role'] for row in graph.sources], ['graph_receipt'])
            self.assertTrue(graph.sources[0]['verified'])
            # Fixture deliberately omits real graph artifacts; their gate must still run.
            self.assertEqual(graph.errors, ["'staffer_social_node_scores'"])
            self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_non_object_verifier_records_controlled_refusal(self):
        for malformed in ([], None, 'bad'):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as directory:
                graph = self.fixture(directory, lambda receipt: None)
                path = Path(directory) / 'audits/staffer_network' / snapshot.VERIFICATION / 'staffer_social_network_verification_receipt.json'
                path.write_text(json.dumps(malformed))
                graph.load()
                self.assertEqual(graph.errors, ['The pinned graph verification has not passed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
