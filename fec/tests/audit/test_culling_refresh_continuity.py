import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import legislation_culling_status as status


def install(tmp_path):
    files = {}
    for name in status.CULLING_CONTINUITY_FILES:
        path = tmp_path / name
        path.write_text(json.dumps({'status': 'PASS_STAGED_INTEGRATION_WITH_HELD_NONSCORING', 'name': name}), encoding='utf-8')
        files[name] = {'sha256': status.sha256(path)}
    files[status.RANK_NAME] = {'sha256': hashlib.sha256(b'original ranks').hexdigest()}
    (tmp_path / status.RANK_NAME).write_text('{"rank":{"A":1}}', encoding='utf-8')
    path = tmp_path / 'audits/legfix5/production_promotion/original/promotion_receipt.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'status': 'PASS_PRODUCTION_INPUTS_PROMOTED',
        'promoted_at': '2026-08-01T00:00:00Z', 'installed': files}), encoding='utf-8')
    return path


def test_rank_refresh_keeps_verified_culling_history(tmp_path):
    receipt = install(tmp_path)
    original = receipt.read_bytes()
    result = status.build_surface_status(tmp_path)
    assert result['phase'] == 'production'
    assert result['rank_relation_to_promotion'] == 'refreshed_after_promotion'
    assert set(result['culling_continuity_sha256']) == set(status.CULLING_CONTINUITY_FILES)
    assert receipt.read_bytes() == original


def test_changed_culling_decision_cannot_inherit_promotion(tmp_path):
    install(tmp_path)
    (tmp_path / 'legislation_hold_ledger.ndjson').write_text('changed', encoding='utf-8')
    assert status.build_surface_status(tmp_path)['phase'] == 'staged'


def test_missing_installed_ledger_cannot_inherit_promotion(tmp_path):
    install(tmp_path)
    (tmp_path / 'legislation_membership_edges.ndjson').unlink()
    assert status.build_surface_status(tmp_path)['phase'] == 'staged'
