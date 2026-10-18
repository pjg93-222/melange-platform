"""Synthetic AST-only alignment checkpoints; never import recovery collectors."""
from __future__ import annotations
import argparse
import ast
import collections
import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import types
from typing import Any, Iterator
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / 'speech/source_media_recovery.py'
RAW = SOURCE.read_bytes()
TREE = ast.parse(RAW.decode('utf-8-sig'))
FUNCTION = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'run_align')
CODE = compile(ast.Module(body=[FUNCTION], type_ignores=[]), str(SOURCE), 'exec',
               flags=__import__('__future__').annotations.compiler_flag)


class RecoveryError(RuntimeError):
    pass


class AlignmentCheckpointTests(unittest.TestCase):
    def run_case(self, acquisition, workers, *, unselected=None, package_filter=False):
        with tempfile.TemporaryDirectory(prefix='fec-align-shape-') as directory:
            root = Path(directory)
            writes, aligned, models = [], [], []
            inventory = root / 'inventory.jsonl'
            rows = [
                {'source_family': 'fixture', 'package_id': 'bad', 'package': 'fixture:bad', 'acquisition': acquisition},
                {'source_family': 'fixture', 'package_id': 'good', 'package': 'fixture:good',
                 'acquisition': {'status': 'acquired', 'items': [{'media_id': 'good', 'wav': str(root / 'unused.wav')}]}}
            ]
            if unselected:
                rows[0]['source_family'] = 'excluded'
            inventory.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
            original = inventory.read_bytes()

            def checked(path):
                self.assertTrue(Path(path).resolve().is_relative_to(root.resolve()))
                return Path(path)

            def read_jsonl(path, required=False):
                path = checked(path)
                if not path.exists() and not required:
                    return []
                return [json.loads(line) for line in path.read_text().splitlines() if line]

            def write_jsonl_atomic(path, values):
                path = checked(path)
                path.write_text(''.join(json.dumps(row) + '\n' for row in values), encoding='utf-8')
                writes.append(path.name)

            def persist(values, path):
                write_jsonl_atomic(path, values)
                return {'fixture': True}

            def write_json_atomic(path, value):
                path = checked(path)
                path.write_text(json.dumps(value), encoding='utf-8')
                writes.append(path.name)

            def align_record(row, *args, **kwargs):
                aligned.append(row['package_id'])
                return []

            def model(*args, **kwargs):
                models.append('stub-only')
                return object()

            fake_whisper = types.ModuleType('faster_whisper')
            fake_whisper.WhisperModel = model
            fake_smile = types.ModuleType('opensmile')
            fake_smile.FeatureSet = types.SimpleNamespace(eGeMAPSv02='fixture')
            fake_smile.FeatureLevel = types.SimpleNamespace(Functionals='fixture')
            fake_smile.Smile = lambda **kwargs: object()
            ns = dict(globals(), read_jsonl=read_jsonl, write_jsonl_atomic=write_jsonl_atomic,
                write_json_atomic=write_json_atomic, persist=persist, align_record=align_record,
                load_member_gap=lambda value: {}, member_gap_priority=lambda *args: 0,
                candidate_discoveries=lambda row: [], CLIP_SCHEMA='fixture-clip-v2',
                CANDIDATE_RECEIPT_SCHEMA='fixture-receipt-v2', candidate_cut_binding=lambda row: 'fixture',
                _candidate_hash=lambda row: 'fixture', utc_now=lambda: '2026-10-15T00:00:00Z',
                sha256_file=lambda path: hashlib.sha256(checked(path).read_bytes()).hexdigest())
            exec(CODE, ns)
            args = argparse.Namespace(inventory=str(inventory), source='fixture' if unselected else '',
                member_gap='', media_root=str(root), model=str(root / 'not-a-model'), cpu_threads=1,
                workers=workers, package_id='good' if package_filter else '', refresh=True,
                max_packages=0, checkpoint_every=1, minimum_ratio=0.9)
            error, code = None, None
            with patch.dict(sys.modules, {'faster_whisper': fake_whisper, 'opensmile': fake_smile,
                                        'av': types.ModuleType('av')}), contextlib.redirect_stdout(io.StringIO()):
                try:
                    code = ns['run_align'](args)
                except RecoveryError as exc:
                    error = str(exc)
            saved = read_jsonl(inventory)
            return {'code': code, 'error': error, 'rows': saved, 'writes': writes,
                    'models': models, 'aligned': aligned, 'inventoryUnchanged': inventory.read_bytes() == original}

    def test_bad_items_checkpoint_and_next_package_continues(self):
        for workers in (1, 2):
            for items in (1, 'bad', {'media_id': 'bad'}, [None], ['bad'], [1], False):
                with self.subTest(workers=workers, items=items):
                    result = self.run_case({'status': 'acquired', 'items': items}, workers)
                    self.assertEqual(result['code'], 2)
                    self.assertIsNone(result['error'])
                    self.assertEqual(result['rows'][0]['alignment']['status'], 'error')
                    self.assertEqual(result['rows'][1]['alignment']['status'], 'no_member_turn_matched')
                    self.assertEqual(result['aligned'], ['good'])
                    self.assertEqual(result['writes'].count('inventory.jsonl'), 2)
                    self.assertIn('candidate_clips.receipt.json', result['writes'])

    def test_valid_items_and_legacy_wav_fallback(self):
        for workers in (1, 2):
            for acquisition in (
                {'status': 'acquired', 'items': [{'media_id': 'bad', 'wav': 'synthetic.wav'}]},
                {'status': 'acquired', 'wav': 'synthetic.wav'},
                {'status': 'acquired', 'items': None, 'wav': 'synthetic.wav'},
                {'status': 'acquired', 'items': [], 'wav': 'synthetic.wav'},
            ):
                with self.subTest(workers=workers, acquisition=acquisition):
                    result = self.run_case(acquisition, workers)
                    self.assertEqual(result['code'], 0)
                    self.assertIsNone(result['error'])
                    self.assertEqual(set(result['aligned']), {'bad', 'good'})

    def test_nonobject_acquisition_refuses_before_model_or_output(self):
        for acquisition in (1, 'bad', [], False):
            with self.subTest(acquisition=acquisition):
                result = self.run_case(acquisition, 2)
                self.assertIn('acquisition must be an object', result['error'])
                self.assertEqual(result['models'], [])
                self.assertEqual(result['aligned'], [])
                self.assertEqual(result['writes'], [])
                self.assertTrue(result['inventoryUnchanged'])

    def test_unselected_malformed_acquisition_is_not_inspected(self):
        for options in ({'unselected': True}, {'package_filter': True}):
            with self.subTest(options=options):
                result = self.run_case('bad', 2, **options)
                self.assertEqual(result['code'], 0)
                self.assertIsNone(result['error'])
                self.assertNotIn('alignment', result['rows'][0])
                self.assertEqual(result['aligned'], ['good'])

    def test_source_bytes_unchanged(self):
        self.assertEqual(SOURCE.read_bytes(), RAW)


if __name__ == '__main__':
    def forbid_external(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo', 'subprocess.Popen'}:
            raise AssertionError('External work forbidden in isolated alignment regression')
    sys.addaudithook(forbid_external)
    unittest.main(verbosity=2)
