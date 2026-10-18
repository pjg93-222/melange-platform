"""Static-import tripwire regressions; all scanned sources are synthetic temp files."""
from pathlib import Path
import importlib.util

import pytest

FEC = Path(__file__).resolve().parents[2]


def inspect_source(tmp_path, monkeypatch, text):
    spec = importlib.util.spec_from_file_location('stale_imports_ast_fixture', FEC / 'audit/audit_stale_imports.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scratch = tmp_path / 'fec'
    package = scratch / 'demo'
    package.mkdir(parents=True)
    for name in ('moved_fixture', 'other_fixture'):
        (package / (name + '.py')).write_text('VALUE = 1\n', encoding='utf-8')
    (scratch / 'consumer.py').write_text(text, encoding='utf-8')
    monkeypatch.setattr(module, 'FEC', str(scratch))
    return module.scan(module.build_ledger())


@pytest.mark.parametrize(('source', 'line'), [
    ('import os, moved_fixture\n', 1),
    ('import os, moved_fixture as renamed\n', 1),
    ('from fec import (\n    moved_fixture,\n)\n', 2),
    ('from fec import (os,\n    moved_fixture as renamed,\n)\n', 2),
    ('if True: import moved_fixture\n', 1),
    ('def f():\n    import moved_fixture\n', 2),
    ('from moved_fixture import (\n    VALUE,\n)\n', 1),
])
def test_static_aliases_and_multiline_imports_are_detected(tmp_path, monkeypatch, source, line):
    findings = inspect_source(tmp_path, monkeypatch, source)
    assert len(findings) == 1, findings
    assert findings[0]['module'] == 'moved_fixture'
    assert findings[0]['line'] == line
    assert set(findings[0]) == {'file', 'line', 'form', 'module', 'text'}


@pytest.mark.parametrize('source', [
    'import os, demo.moved_fixture\n',
    'from demo import moved_fixture\n',
    'from .moved_fixture import VALUE\n',
    '# import moved_fixture\n',
    '"""Example only:\nimport moved_fixture\nfrom fec import moved_fixture\n"""\n',
    'from fec import (  # stale-ok: deliberate test fallback\n    moved_fixture,\n)\n',
    'import os, moved_fixture  # stale-ok: deliberate test fallback\n',
])
def test_qualified_noncode_and_explicit_pragma_are_accepted(tmp_path, monkeypatch, source):
    assert inspect_source(tmp_path, monkeypatch, source) == []


def test_multiline_pragma_applies_only_to_its_alias(tmp_path, monkeypatch):
    source = ('from fec import (\n'
              '    moved_fixture,  # stale-ok: deliberate test fallback\n'
              '    other_fixture,\n'
              ')\n')
    findings = inspect_source(tmp_path, monkeypatch, source)
    assert len(findings) == 1 and findings[0]['module'] == 'other_fixture'
    assert findings[0]['line'] == 3


def test_syntax_error_keeps_legacy_static_tripwire(tmp_path, monkeypatch):
    findings = inspect_source(tmp_path, monkeypatch, 'import moved_fixture\nif:\n')
    assert len(findings) == 1 and findings[0]['form'] == 'bare-import'


@pytest.mark.parametrize(('source', 'form'), [
    ("value = __import__('moved_fixture')\n", 'dynamic-string'),
    ("value = import_module('moved_fixture')\n", 'dynamic-string'),
    ("spec = spec_from_file_location('alias', FEC / 'moved_fixture.py')\n", 'flat-py-path'),
])
def test_dynamic_and_path_tripwires_remain(tmp_path, monkeypatch, source, form):
    findings = inspect_source(tmp_path, monkeypatch, source)
    assert len(findings) == 1 and findings[0]['form'] == form
