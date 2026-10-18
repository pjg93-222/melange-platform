"""Planted stale imports must fail; canonical package imports and paths must pass."""
import ast
import os
from pathlib import Path
import re
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]


def scanner(scratch):
    # Lift only scanner definitions. No production module or engine is imported.
    path = FEC / 'audit/audit_stale_imports.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    wanted = {'build_ledger', 'iter_sources', 'scan'}
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert {node.name for node in body} == wanted
    namespace = {'os': os, 're': re, '__file__': str(path), 'FEC': str(scratch),
                 'SKIP_DIRS': {'tests', 'attic', 'templates', '__pycache__', 'liwc', 'lep'}}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def inspect_source(tmp_path, source):
    fec = tmp_path / 'fec'
    pkg = fec / 'demo'
    pkg.mkdir(parents=True)
    (pkg / 'moved_fixture.py').write_text('VALUE = 1\n', encoding='utf-8')
    (fec / 'consumer.py').write_text(source, encoding='utf-8')
    functions = scanner(fec)
    ledger = functions['build_ledger']()
    assert ledger['moved_fixture'] == 'demo'
    return functions['scan'](ledger)


@pytest.mark.parametrize(('source', 'form'), [
    ('import moved_fixture\n', 'bare-import'),
    ('import moved_fixture as module\n', 'bare-import'),
    ('from moved_fixture import VALUE\n', 'bare-import'),
    ('from fec import moved_fixture\n', 'from-fec'),
    ("value = __import__('moved_fixture')\n", 'dynamic-string'),
    ("value = import_module('moved_fixture')\n", 'dynamic-string'),
    ("spec = spec_from_file_location('alias', FEC / 'moved_fixture.py')\n", 'flat-py-path'),
])
def test_planted_stale_resolution_is_detected(tmp_path, source, form):
    findings = inspect_source(tmp_path, source)
    assert len(findings) == 1, findings
    assert findings[0]['module'] == 'moved_fixture' and findings[0]['form'] == form
    assert findings[0]['line'] == 1


@pytest.mark.parametrize('source', [
    'from demo import moved_fixture\n',
    'from demo.moved_fixture import VALUE\n',
    'import demo.moved_fixture as module\n',
    "module = import_module('demo.moved_fixture')\n",
    "spec = spec_from_file_location('alias', FEC / 'demo' / 'moved_fixture.py')\n",
])
def test_qualified_resolution_is_accepted(tmp_path, source):
    assert inspect_source(tmp_path, source) == []


def test_inserting_sibling_directory_does_not_exempt_a_bare_import(tmp_path):
    findings = inspect_source(tmp_path, "sys.path.insert(0, str(FEC / 'demo'))\nfrom moved_fixture import VALUE\n")
    assert len(findings) == 1 and findings[0]['form'] == 'bare-import'


def test_clean_workspace_loader_names_the_actual_package_path():
    path = FEC / 'tests/surfaces/test_clean_workspace_loading.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    builder = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'builder')
    call = next(node for node in ast.walk(builder) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == 'spec_from_file_location')
    resolved = eval(compile(ast.Expression(call.args[1]), str(path), 'eval'), {'ROOT': FEC.parent})
    assert resolved == FEC / 'surfaces/build_clean_workspace.py'
    assert resolved.is_file()
