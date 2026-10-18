"""Selected synthetic inventories must never be mistaken for readiness gates."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.mark.parametrize('populated,total', [(0, 0), (0, 2), (1, 2), (2, 2)])
def test_counts_do_not_invent_readiness_or_finalization(populated, total, tmp_path, monkeypatch, capsys):
    source = Path(__file__).resolve().parents[2] / 'audit/audit_pre_culling_inventory.py'
    spec = importlib.util.spec_from_file_location('synthetic_pre_culling_inventory', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # stdlib-only definitions; main remains guarded
    evidence = tmp_path / 'evidence.ndjson'
    rows = [{'canonical_key': f'synthetic-{index}',
             'category_evidence': {'fixture': True} if index < populated else {}}
            for index in range(total)]
    evidence.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    billstatus, full_text = tmp_path / 'billstatus', tmp_path / 'full-text'
    billstatus.mkdir()
    full_text.mkdir()
    monkeypatch.setattr(sys, 'argv', ['inventory', '--evidence', str(evidence),
                                    '--billstatus', str(billstatus), '--full-text-receipts', str(full_text)])
    assert module.main() == 0
    output = capsys.readouterr().out
    assert f'category_evidence empty on {total - populated} / {total} candidates' in output
    assert 'category_audit_ready = 0' not in output
    assert 'semantic_review_ready = 0' not in output
    assert 'membership_is_finalizable is False corpus-wide' not in output
    assert 'readiness is not assessed' in output
    assert 'finalization require their own validation' in output
