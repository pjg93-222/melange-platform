"""Isolated receipt/error regressions; never runs a producer against live data.

Only the two import-reviewed, main-guarded surface modules are imported. All
path defaults are redirected before import; fixture files and writes stay in
TemporaryDirectory. No pipeline, glassbox, network, workbook, or live renderer.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(os.environ.get('FEC_SURFACE_SOURCE_ROOT', Path(__file__).resolve().parents[3]))
_ISOLATION = tempfile.TemporaryDirectory(prefix='surface-gate-imports-')
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO))
with mock.patch.dict(os.environ, {key: _ISOLATION.name for key in (
        'MONEYBALL_ROOT', 'MELANGE_LOCAL_ROOT', 'MELANGE_DATA_ROOT', 'STAFFER_NETWORK_LOCAL')}):
    network = importlib.import_module('fec.surfaces.build_staffer_network_section')
    groups = importlib.import_module('fec.surfaces.build_staffer_groups_section')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')


_UNSET = object()


class SurfaceGateTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='surface-gate-case-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def test_sidecar_requires_receipt_entry_and_nonempty_hash(self):
        base = self.root / 'audits/staffer_network/sidecar'
        base.mkdir(parents=True)
        names = (groups.SIDECAR_REGISTRANTS, groups.SIDECAR_SUMMARY)
        specs = {}
        for name in names:
            data = ('synthetic:' + name).encode()
            (base / name).write_bytes(data)
            specs[name] = {'bytes': len(data), 'sha256': sha(data)}
        receipt = {'status': 'PASS', 'failures': [], 'files': specs}
        receipt_path = base / groups.SIDECAR_RECEIPT
        write_json(receipt_path, receipt)
        result = groups.bind_sidecar(str(self.root), 'sidecar')
        self.assertEqual(set(result['paths']), {'registrant_side_records', 'tiered_person_summary'})
        for invalid in (None, {}, {'sha256': ''}, {'sha256': ' '}, {'sha256': '0' * 64}):
            with self.subTest(invalid=invalid):
                modified = dict(specs)
                if invalid is None:
                    del modified[names[0]]
                else:
                    modified[names[0]] = invalid
                write_json(receipt_path, {**receipt, 'files': modified})
                with self.assertRaises(groups.InputContradiction):
                    groups.bind_sidecar(str(self.root), 'sidecar')

    def test_bind_file_preserves_size_and_digest_gates(self):
        path = self.root / 'input.bin'
        path.write_bytes(b'fixture')
        for spec in ({'bytes': 1, 'sha256': sha(b'fixture')},
                     {'bytes': 7, 'sha256': sha(b'changed')}):
            with self.subTest(spec=spec), self.assertRaises(groups.InputContradiction):
                groups.bind_file(str(path), spec, 'fixture', ['receipt'])

    def test_cleanup_failure_does_not_replace_original_write_failure(self):
        target = self.root / 'output.json'
        target.write_bytes(b'previous valid artifact')
        original = OSError('synthetic rename failure')
        with mock.patch.object(groups.os, 'replace', side_effect=original), \
                mock.patch.object(groups.os, 'remove', side_effect=PermissionError('cleanup blocked')):
            with self.assertRaises(OSError) as raised:
                groups.write_all_atomic([(str(target), b'new artifact')])
        self.assertIs(raised.exception, original)
        self.assertEqual(target.read_bytes(), b'previous valid artifact')
        self.assertEqual(len(list(self.root.glob('.staffer_groups_section.*.tmp'))), 1)

    def test_successful_atomic_write_leaves_no_staged_file(self):
        first, second = self.root / 'a.json', self.root / 'b.json'
        attempts = groups.write_all_atomic([(str(first), b'a'), (str(second), b'b')])
        self.assertEqual(first.read_bytes(), b'a')
        self.assertEqual(second.read_bytes(), b'b')
        self.assertEqual(set(attempts.values()), {1})
        self.assertEqual(list(self.root.glob('.staffer_groups_section.*.tmp')), [])

    def test_employment_contradiction_is_non_successful_cli_refusal(self):
        (self.root / 'audits/staffer_network/fixture_primary').mkdir(parents=True)
        output = io.StringIO()
        with mock.patch.object(groups, 'build', side_effect=groups.lda_employment.EmploymentContradiction(
                'synthetic employment contradiction')), contextlib.redirect_stdout(output):
            result = groups.main(['--root', str(self.root), '--generation', 'fixture_primary', '--no-glassbox'])
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue(), 'REFUSED: synthetic employment contradiction\n')
        self.assertFalse((self.root / 'data').exists())

    def test_cli_refusal_is_bounded_single_line(self):
        (self.root / 'audits/staffer_network/fixture_primary').mkdir(parents=True)
        output = io.StringIO()
        with mock.patch.object(groups, 'build', side_effect=groups.InputContradiction(
                'invalid\r\ninput\t' + 'x' * 900)), contextlib.redirect_stdout(output):
            result = groups.main(['--root', str(self.root), '--generation', 'fixture_primary', '--no-glassbox'])
        self.assertEqual(result, 2)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertTrue(output.getvalue().startswith('REFUSED: invalid input '))
        self.assertLessEqual(len(output.getvalue()), len('REFUSED: ') + 801)

    def test_node_identity_refused_before_dictionary_index(self):
        for kind in ('person', 'container'):
            for identity in (None, '', [], 3):
                with self.subTest(kind=kind, identity=identity):
                    row = {'node_kind': kind}
                    if identity is not None:
                        row['node_id'] = identity
                    path = self.root / 'nodes.ndjson.gz'
                    with gzip.open(path, 'wt', encoding='utf-8') as stream:
                        stream.write(json.dumps(row) + '\n')
                    with self.assertRaises(network.InputContradiction):
                        network.read_node_scores(str(path), {})

    def test_legacy_container_retains_identity_and_office_metadata(self):
        path = self.root / 'nodes.ndjson.gz'
        with gzip.open(path, 'wt', encoding='utf-8') as stream:
            stream.write(json.dumps({'node_kind': 'container', 'node_id': 'container:fixture',
                                     'office_key': 'office-fixture', 'member_bioguide': 'F000001'}) + '\n')
        result = network.read_node_scores(str(path), {})
        self.assertEqual(result['container_office_keys'], {'container:fixture': 'office-fixture'})
        self.assertEqual(result['container_bioguides'], {'container:fixture': 'F000001'})
        self.assertTrue(result['has_container_rows'])

    def generation_fixture(self, *, declare_groups=True, group_override=None,
                           recorded_override=None, omit_groups_file=False,
                           verifier_groups=True, receipt_files_override=_UNSET):
        generation = 'fixture_primary'
        base = self.root / 'audits/staffer_network' / generation
        base.mkdir(parents=True, exist_ok=True)
        entries, integrity = [], []
        for role, name in network.CONSUMED_FILES:
            data = ('independent fixture bytes for ' + role).encode()
            (base / name).write_bytes(data)
            entries.append({'role': role, 'path': name, 'sha256': sha(data)})
        name = 'staffer_social_groups.ndjson.gz'
        data = b'independent groups fixture bytes'
        if declare_groups:
            if not omit_groups_file:
                (base / name).write_bytes(data)
            entries.append({'role': 'groups', 'path': name, 'sha256': sha(data), **(group_override or {})})
            integrity.append({'path': name, 'receipt_sha256': sha(data),
                              'recomputed_sha256': sha(data), **(recorded_override or {})})
        receipt = {'review_status': 'REVIEW_REQUIRED',
                   'files': entries if receipt_files_override is _UNSET else receipt_files_override,
                   'canonical_employment_promotions': 0, 'canonical_identity_promotions': 0}
        write_json(base / network.RECEIPT_NAME, receipt)
        write_json(base / network.MANIFEST_NAME, {'fixture': True})
        twin = [{'path': name, 'primary_sha256': sha((base / name).read_bytes())}
                for name in (network.RECEIPT_NAME, network.MANIFEST_NAME)]
        verification = {'status': 'PASS', 'failures': [], 'twin_byte_identity': twin,
                        'primary_file_integrity': integrity if verifier_groups else []}
        write_json(self.root / 'audits/staffer_network' / network.verification_dir_name(generation)
                   / network.VERIFICATION_NAME, verification)
        return generation

    def test_groups_bound_to_independent_verifier_hashes(self):
        generation = self.generation_fixture()
        result = network.bind_inputs(str(self.root), generation)
        self.assertEqual(Path(result['paths']['groups']).name, 'staffer_social_groups.ndjson.gz')
        self.assertEqual(result['bound']['staffer_social_groups']['bound_against'], [
            'generation_receipt.files',
            'verification_receipt.primary_file_integrity.receipt_sha256',
            'verification_receipt.primary_file_integrity.recomputed_sha256'])

    def test_groups_reject_poisoned_verifier_hash_and_missing_hash(self):
        for field in ('receipt_sha256', 'recomputed_sha256'):
            for value in ('0' * 64, None, ''):
                with self.subTest(field=field, value=value):
                    generation = self.generation_fixture(recorded_override={field: value})
                    with self.assertRaises(network.InputContradiction):
                        network.bind_inputs(str(self.root), generation)

    def test_groups_helper_requires_each_recorded_verifier_hash(self):
        generation = self.generation_fixture()
        valid = network.bind_inputs(str(self.root), generation)
        for field in ('receipt_sha256', 'recomputed_sha256'):
            for value in (None, ''):
                with self.subTest(field=field, value=value):
                    verification = {'primary_file_integrity': [{
                        'path': 'staffer_social_groups.ndjson.gz',
                        'receipt_sha256': sha(b'independent groups fixture bytes'),
                        'recomputed_sha256': sha(b'independent groups fixture bytes'), field: value}]}
                    with self.assertRaises(groups.InputContradiction):
                        groups.bind_generation_groups({**valid, 'verification': verification})

    def test_groups_reject_wrong_name_and_absent_or_empty_declared_hash(self):
        for value in ({'path': '../outside.ndjson.gz'}, {'sha256': None}, {'sha256': ''}):
            with self.subTest(value=value):
                generation = self.generation_fixture(group_override=value)
                with self.assertRaises(network.InputContradiction):
                    network.bind_inputs(str(self.root), generation)

    def test_declared_groups_file_must_exist(self):
        generation = self.generation_fixture(omit_groups_file=True)
        with self.assertRaises(network.InputContradiction):
            network.bind_inputs(str(self.root), generation)

    def test_declared_groups_requires_independent_integrity_row(self):
        generation = self.generation_fixture(verifier_groups=False)
        with self.assertRaisesRegex(network.InputContradiction, 'no integrity row'):
            network.bind_inputs(str(self.root), generation)

    def test_direct_groups_helper_requires_independent_integrity_row(self):
        generation = self.generation_fixture()
        valid = network.bind_inputs(str(self.root), generation)
        with self.assertRaisesRegex(groups.InputContradiction, 'no integrity row'):
            groups.bind_generation_groups({**valid, 'verification': {'primary_file_integrity': []}})

    def test_malformed_receipt_file_lists_refuse_in_both_binders(self):
        generation = self.generation_fixture()
        valid = network.bind_inputs(str(self.root), generation)
        for malformed in (None, {}, 'groups', [None], ['groups'], [3]):
            with self.subTest(malformed=malformed):
                generation = self.generation_fixture(receipt_files_override=malformed)
                with self.assertRaisesRegex(network.InputContradiction, 'list of objects'):
                    network.bind_inputs(str(self.root), generation)
                with self.assertRaisesRegex(groups.InputContradiction, 'list of objects'):
                    groups.bind_generation_groups({**valid, 'receipt': {'files': malformed}})


    def test_absent_v5_groups_remains_compatible(self):
        generation = self.generation_fixture(declare_groups=False)
        result = network.bind_inputs(str(self.root), generation)
        self.assertNotIn('groups', result['paths'])
        self.assertNotIn('staffer_social_groups', result['bound'])


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        _ISOLATION.cleanup()
