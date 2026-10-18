"""Only explicit temporary receipt trees; no repository audit data."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.fixture
def auditor():
    path = Path(__file__).resolve().parents[2] / 'audit/audit_legfix5_artifact_gaps.py'
    spec = importlib.util.spec_from_file_location('fixture_gap_auditor', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('declared', [None, True, '3', 3])
def test_size_verification_requires_an_actual_declared_integer(tmp_path, auditor, declared):
    body = tmp_path / 'body.txt'
    body.write_bytes(b'abc')
    report = auditor.audit_paths({body: {'bytes': declared, 'sha256': None}})
    assert report['size_verified'] == int(type(declared) is int)
    assert report['size_undeclared'] == int(type(declared) is not int)
    assert not report['size'] and not report['missing']


@pytest.mark.parametrize('malformed', [False, True])
def test_unreadable_receipt_prevents_success_exit(tmp_path, auditor, monkeypatch, capsys, malformed):
    receipt = tmp_path / 'receipt.json'
    receipt.write_text('{' if malformed else json.dumps({'path': 'body.txt', 'bytes': 3}), encoding='utf-8')
    (tmp_path / 'body.txt').write_bytes(b'abc')
    monkeypatch.setattr(auditor, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['artifact-gap-fixture', '--root', str(tmp_path)])
    assert auditor.main() == int(malformed)
    output = capsys.readouterr().out
    if malformed:
        assert 'unreadable receipts: 1' in output
    else:
        assert 'present and size-verified: 1' in output
