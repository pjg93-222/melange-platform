"""Real evidence readers use the selected desktop workspace, never package data."""
import gzip
import gc
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGING = Path(__file__).resolve().parents[1]
REPOSITORY = PACKAGING.parents[2]
sys.path[:0] = [str(PACKAGING), str(REPOSITORY)]
from melange_data_paths import configure_melange_workspace, WORKSPACE_FILES
from fec.surfaces import staffer_person_inventory as inventory
from fec.surfaces import staffer_identity_web as identity_web
from fec.surfaces import staffer_routing_evidence as evidence
from fec.surfaces import staffer_payroll_identity as payroll


class WorkspaceDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.temp.name) / 'workspace'
        self.files = self.root / 'fec' / 'surfaces'
        self.files.mkdir(parents=True)
        for name, attributes in WORKSPACE_FILES.items():
            module = __import__('fec.surfaces.' + name, fromlist=['x'])
            for attribute in attributes:
                original = getattr(module, attribute)
                self.addCleanup(setattr, module, attribute, original)

    def write(self, name, value):
        target = self.files / name
        target.write_text(json.dumps(value), encoding='utf-8')
        return target

    def test_actual_inventory_and_seed_readers_use_workspace(self):
        assertions = self.write('staffer_user_assertions.json', {'identity_groups': []})
        self.write('staffer_identity_reference_seeds.json', {'references': [
            {'url': 'https://example.org/reference', 'eligible_for_reference_fetch': True}]})
        configure_melange_workspace(self.root)
        result = {'nodes': {}, 'sources': []}
        inventory.apply_identity_overlay(result, {})
        self.assertEqual(result['sources'][0]['path'], str(assertions.resolve()))
        self.assertEqual(result['sources'][0]['sha256'], hashlib.sha256(assertions.read_bytes()).hexdigest())
        index = identity_web.IdentityWebIndex(self.root)
        self.assertEqual(len(index.seeds), 1)
        self.assertEqual(index.trusted, {'example.org'})

    def test_actual_payroll_default_loader_and_hash_gate(self):
        bundle = self.root / 'evidence.json.gz'
        with gzip.open(bundle, 'wt', encoding='utf-8') as stream:
            json.dump({'version': payroll.VERSION, 'sources': [], 'episodes': [],
                       'record_pairs': [], 'counts': {'episodes': 0}}, stream)
        config = self.write('staffer_payroll_identity.json', {
            'path': bundle.name, 'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest()})
        configure_melange_workspace(self.root)
        self.assertEqual(payroll.load_payroll_identity(self.root)['counts'], {'episodes': 0})
        value = json.loads(config.read_text()); value['sha256'] = '0' * 64
        config.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            payroll.load_payroll_identity(self.root)

    def test_actual_named_evidence_reader_and_hash_gate(self):
        corpus = self.root / 'lda'; corpus.mkdir()
        (corpus / 'lda_source_native_corpus_receipt.json').write_text(json.dumps({
            'status': 'PASS', 'failures': [], 'files': [{'path': 'lda_industry_edges.ndjson.gz'}]}))
        named = self.write('staffer_named_filing_evidence.json', {'sources': [], 'shared_activities': []})
        configure_melange_workspace(self.root)
        result = {'sources': [], 'named_examples': {}}
        with patch.object(evidence, 'NAMED_SHA256', hashlib.sha256(named.read_bytes()).hexdigest()):
            evidence.add_named_activities(result, corpus)
            self.assertEqual(result['sources'][0]['path'], str(named.resolve()))
            named.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                evidence.add_named_activities(result, corpus)

    def test_missing_files_are_not_created_or_silently_substituted(self):
        configure_melange_workspace(self.root)
        self.assertEqual(list(self.files.iterdir()), [])
        with self.assertRaises(FileNotFoundError):
            inventory.apply_identity_overlay({'nodes': {}, 'sources': []}, {})
        (self.root / 'audits/staffer_network/hill_personnel_spine_fixture_primary').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'configuration missing'):
            payroll.load_payroll_identity(self.root)

    def test_reject_directory_and_canonical_escape_before_binding(self):
        old = inventory.USER_ASSERTIONS
        (self.files / 'staffer_user_assertions.json').mkdir()
        with self.assertRaisesRegex(ValueError, 'regular data file'):
            configure_melange_workspace(self.root)
        self.assertEqual(inventory.USER_ASSERTIONS, old)
        (self.files / 'staffer_user_assertions.json').rmdir()
        outside = Path(self.temp.name) / 'outside.json'; outside.write_text('{}')
        resolve = Path.resolve
        def escaped(path, *args, **kwargs):
            return outside if path.name == 'staffer_user_assertions.json' else resolve(path, *args, **kwargs)
        # Model canonical resolution of a junction/symlink on hosts where creating
        # symlinks requires privileges; the actual resolver remains in production.
        with patch.object(Path, 'resolve', escaped):
            with self.assertRaisesRegex(ValueError, 'remain inside'):
                configure_melange_workspace(self.root)
        self.assertEqual(inventory.USER_ASSERTIONS, old)


if __name__ == '__main__':
    unittest.main()
