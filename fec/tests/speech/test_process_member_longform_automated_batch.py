import json
import subprocess
import sys

from fec.speech import process_member_longform_automated_batch as batch


def test_invoke_passes_step_timeout(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(batch.subprocess, 'run', lambda *a, **k: seen.update(k))
    monkeypatch.setattr(batch, 'STEP_TIMEOUT', 7)
    batch.invoke('m', [], tmp_path / 'log')
    assert seen['timeout'] == 7


def test_hung_child_holds_member_and_batch_continues(tmp_path, monkeypatch):
    members = [{'member': 'A', 'status': 'SOURCE_X', 'source_id': 's'},
               {'member': 'B', 'status': 'SOURCE_HOLD', 'reason': 'r'}]
    monkeypatch.setattr(batch, 'check_prepare', lambda root: {'roles': tmp_path / 'roles.json'})
    (tmp_path / 'roles.json').write_text(json.dumps({'members': {}}))
    monkeypatch.setattr(batch.snapshot, 'rows_by_member', lambda rows, label: {'A': {}})
    monkeypatch.setattr(batch.SMR, 'read_jsonl', lambda path, required=False: members)
    monkeypatch.setattr(batch, 'choose_transcript', lambda *a: ({'transcript_sha256': 'x' * 16}, []))
    monkeypatch.setattr(batch, 'freeze_source', lambda *a: {})

    def hang(*a, **k):
        raise subprocess.TimeoutExpired('cmd', 1)
    monkeypatch.setattr(batch, 'source_pipeline', hang)
    monkeypatch.setattr(batch.SMR, 'write_jsonl_atomic', lambda *a: None)
    monkeypatch.setattr(batch.SMR, 'write_json_atomic', lambda *a: None)
    monkeypatch.setattr(batch.SMR, 'sha256_file', lambda p: 'h')
    monkeypatch.setattr(sys, 'argv', ['x', '--batch-root', str(tmp_path), '--converted-model', 'm',
                                      '--step-timeout', '1'])
    assert batch.main() == 0
