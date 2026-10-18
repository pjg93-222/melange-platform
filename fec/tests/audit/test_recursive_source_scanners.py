"""Exercise only scanner discovery definitions against an isolated source tree."""
import ast
import os
from pathlib import Path

import pytest


@pytest.fixture(params=['audit_path_resolution.py', 'audit_output_consumption.py'])
def scanner(request, tmp_path):
    source = Path(__file__).resolve().parents[2] / 'audit' / request.param
    tree = ast.parse(source.read_bytes(), filename=str(source))
    definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_source_files')
    namespace = {'FEC': tmp_path / 'fec', 'Path': Path, 'os': os}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_source_files'], namespace['FEC']


def create(root, name):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("raise RuntimeError('scanner must never import a source')\n", encoding='utf-8')
    return path


def test_discovers_relocated_and_nested_sources(scanner):
    scan, root = scanner
    names = ['root.py', 'audit/check.py', 'engine/producer.py', 'collect/deep/worker.py',
             'surfaces/templates/view.tpl', 'templates/root.tpl', 'desktop/launcher.pyw',
             'tests/nested/test_support.py']
    expected = {create(root, name) for name in names}
    create(root, 'data/receipt.json')
    create(root, 'notes.md')
    assert set(scan()) == expected


def test_preserves_root_audit_test_and_template_support(scanner):
    scan, root = scanner
    expected = {create(root, name) for name in ['root.py', 'audit/check.py', 'tests/test_one.py', 'templates/view.tpl']}
    assert set(scan()) == expected
    assert scan() == sorted(expected, key=lambda path: str(path).lower())


def test_excludes_archives_dependencies_and_caches(scanner):
    scan, root = scanner
    for folder in ['attic', '_quarantine_paulcomputer_20260727', '_quarantine_new',
                   '__pycache__', '.cache', '.pytest_cache', '.mypy_cache', '.ruff_cache',
                   '.git', 'node_modules', '.venv', 'venv']:
        create(root, folder + '/nested/do_not_scan.py')
        create(root, 'engine/' + folder + '/do_not_scan.tpl')
    assert scan() == []


@pytest.mark.parametrize('filename', ['audit_path_resolution.py', 'audit_output_consumption.py'])
@pytest.mark.parametrize('denied', [False, True])
def test_audit_reports_denied_discovery_without_running_sources(tmp_path, monkeypatch, filename, denied):
    # Lift definitions only; do not execute module bootstrap, imports or live-root scans.
    import json
    source = Path(__file__).resolve().parents[2] / 'audit' / filename
    tree = ast.parse(source.read_bytes(), filename=str(source))
    definitions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    root = tmp_path / 'isolated-root'
    fec = root / 'fec'
    namespace = {'ROOT': root, 'FEC': fec, 'Path': Path, 'os': os, 'json': json,
                 'PATH_CATEGORY_REASONS': {}, 'OUTPUT_CATEGORY_REASONS': {},
                 'WRITE_ONLY_ALLOWLIST': set(), 'WRITE_ONLY_ALLOWLIST_REASON': 'fixture'}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), namespace)
    seen = []
    def fixture_walk(path, followlinks=False, onerror=None):
        assert Path(path) == fec and followlinks is False
        seen.append(True)
        if denied and onerror:
            onerror(PermissionError(13, 'synthetic denied subtree', str(fec / 'engine')))
        return iter(())
    monkeypatch.setattr(os, 'walk', fixture_walk)
    result = namespace['audit']()
    assert seen == [True]
    assert result['clean'] is (not denied)
    assert result['summary']['source_parse_findings'] == int(denied)
    if denied:
        assert result['findings'][0]['kind'] == 'source_unreadable_or_unparseable'
        assert result['findings'][0]['source'] == os.path.join('fec', 'engine')
        assert 'source discovery failed' in result['findings'][0]['detail']
