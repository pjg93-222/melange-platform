"""Exercise status/exit from existing gates, without importing math or live inputs."""
import ast
import datetime as dt
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('placements', [False, True])
def test_boundary_receipt_status_and_exit_follow_existing_gates(tmp_path, placements):
    source = Path(__file__).resolve().parents[2] / 'audit/audit_tier_boundary_delta.py'
    tree = ast.parse(source.read_bytes(), filename=str(source))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    required = ['Brad Sherman', 'Bill Huizenga', 'Young Kim', 'Catherine Cortez Masto', 'Tim Scott']
    names = required + ['Andy Barr', 'French Hill'] + [f'Synthetic {n}' for n in range(78)]
    scores = {name: 1000 - i for i, name in enumerate(names)}
    documents = {
        'member_desk_bundle.json': {'members': [{'name': n} for n in names]},
        'ranks_python.json': {'pcts': {}},
        'data/engine/primacy_factors.json': {'members': {n: {'factor_shrunk': 1} for n in names}},
    }
    def board(given_names, given_scores, buckets):
        assert given_names == names and given_scores == scores
        a, b, c = buckets
        tiers = {n: 1 if i < a else 2 if i < a+b else 3 if i < a+b+c else 0
                 for i, n in enumerate(names)}
        return names, tiers, {n: i+1 for i, n in enumerate(names)}
    tu = SimpleNamespace(BUCKETS=(13,24,48),
                         primacy_scores=lambda *a, **k: scores,
                         _cutoffs=lambda b: (b[0], sum(b[:2]), sum(b)),
                         load_tier_overrides=lambda: {'Synthetic placement': 1} if placements else {},
                         load_tier_positions=lambda: {})
    output = tmp_path / 'receipt.json'
    namespace = {'TU': tu, 'ROOT': tmp_path, 'OUTPUT': output, 'BEFORE': (18,24,48),
                 'AFTER': (13,24,48), 'load_json': documents.__getitem__, 'make_board': board,
                 'sha256': lambda p: '0' * 64, 'dt': dt, 'json': json, 'os': os}
    exec(compile(ast.Module(body=[main], type_ignores=[]), str(source), 'exec'), namespace)
    result = namespace['main']()
    receipt = json.loads(output.read_text(encoding='utf-8'))
    assert receipt['gates']['no_display_placements'] is (not placements)
    assert all(value for key, value in receipt['gates'].items() if key != 'no_display_placements')
    assert result == int(placements)
    assert receipt['status'] == ('FAIL_AUTHORIZED_TIER13_RECOMPUTE' if placements else 'PASS_AUTHORIZED_TIER13_RECOMPUTE')
