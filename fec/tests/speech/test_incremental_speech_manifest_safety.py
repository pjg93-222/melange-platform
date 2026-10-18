"""Isolated preservation checks; no network, collector import or live data."""
from __future__ import annotations
import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]

class IncrementalSpeechManifestSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'fec/speech/discover_member_longform_interviews.py'
        spec = importlib.util.spec_from_file_location('isolated_incremental_discovery', path)
        cls.discovery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.discovery)
        parser_path = ROOT / 'fec/speech/houselive_collect.py'
        tree = ast.parse(parser_path.read_text(encoding='utf-8-sig'))
        parser = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'read_manifest')
        namespace = {'Path': Path, 'json': json}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[parser], type_ignores=[])), str(parser_path), 'exec'), namespace)
        cls.read_manifest = staticmethod(namespace['read_manifest'])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='speech-manifest-safety-')
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.roster = self.root / 'roster.json'
        self.candidates = self.root / 'candidates.jsonl'
        self.coverage = self.root / 'coverage.json'
        self.roster.write_text(json.dumps({'members': {'Fixture Alpha': {}, 'Fixture Beta': {}}}), encoding='utf-8')

    def prior(self, candidates=True, coverage=True):
        if candidates:
            self.candidates.write_text(json.dumps({'member': 'Fixture Beta', 'candidate_rank': 1}) + '\n', encoding='utf-8')
        if coverage:
            self.coverage.write_text(json.dumps({'members': {'Fixture Beta': {'search_status': 'SEARCHED', 'qualifying_candidates': 1}}}), encoding='utf-8')

    def argv(self, flags):
        return ['discovery', '--roster', str(self.roster), '--out', str(self.candidates), '--coverage', str(self.coverage), '--receipt', str(self.root / 'receipt.json'), '--fallback', str(self.root / 'fallback.jsonl'), '--workers', '1', *flags]

    def invoke(self, flags, fake_fetch):
        with patch.object(sys, 'argv', self.argv(flags)), patch.object(self.discovery, 'fetch', side_effect=fake_fetch), patch.object(self.discovery, 'urlopen', side_effect=AssertionError('Network forbidden')), contextlib.redirect_stdout(io.StringIO()):
            return self.discovery.main()

    def assert_partial_pair_refused(self, candidates, coverage, flags):
        self.prior(candidates, coverage)
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        calls = []
        with self.assertRaisesRegex(SystemExit, 'prior coverage and candidate outputs'):
            self.invoke(flags, lambda *args: calls.append(args))
        self.assertEqual(calls, [])
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)

    def test_member_with_candidate_only_refuses_before_network_or_write(self):
        self.assert_partial_pair_refused(True, False, ['--member', 'Fixture Alpha'])

    def test_member_with_coverage_only_refuses_before_network_or_write(self):
        self.assert_partial_pair_refused(False, True, ['--member', 'Fixture Alpha'])

    def test_resume_with_partial_pair_remains_refused(self):
        self.assert_partial_pair_refused(True, False, ['--resume-errors'])

    def test_emit_with_no_prior_pair_remains_refused(self):
        self.assert_partial_pair_refused(False, False, ['--emit-from-existing'])

    def test_member_with_complete_pair_preserves_other_members(self):
        self.prior()
        self.assertEqual(self.invoke(['--member', 'Fixture Alpha'], lambda *a: ('synthetic', [], None)), 0)
        rows = [json.loads(line) for line in self.candidates.read_text().splitlines()]
        self.assertEqual([row['member'] for row in rows], ['Fixture Beta'])
        self.assertIn('Fixture Beta', json.loads(self.coverage.read_text())['members'])

    def test_fresh_member_with_neither_prior_output_remains_supported(self):
        self.assertEqual(self.invoke(['--member', 'Fixture Alpha'], lambda *a: ('synthetic', [], None)), 0)
        self.assertEqual(self.candidates.read_bytes(), b'')
        self.assertEqual(set(json.loads(self.coverage.read_text())['members']), {'Fixture Alpha'})

    def test_houselive_duplicate_date_refuses_without_modifying_manifest(self):
        path = self.root / 'manifest.jsonl'
        original = b'{"date":"2020-01-01","fixture":"first"}\n{"date":"2020-01-01","fixture":"second"}\n'
        path.write_bytes(original)
        with self.assertRaisesRegex(RuntimeError, 'duplicate manifest date'):
            self.read_manifest(path)
        self.assertEqual(path.read_bytes(), original)

    def test_houselive_unique_dates_preserve_both_rows(self):
        path = self.root / 'manifest.jsonl'
        path.write_text('{"date":"2020-01-01"}\n{"date":"2020-01-02"}\n', encoding='utf-8')
        self.assertEqual(set(self.read_manifest(path)), {'2020-01-01', '2020-01-02'})

    def test_houselive_malformed_line_remains_refused(self):
        path = self.root / 'manifest.jsonl'
        path.write_text('not JSON\n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'invalid JSON'):
            self.read_manifest(path)

if __name__ == '__main__':
    unittest.main(verbosity=2)
