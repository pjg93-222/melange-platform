"""Synthetic isolation and fail-closed runner regression for the staffer HTML suite.

The HTML test module is import-safe. Only its fixture helpers are imported; the
kernel runner is AST-lifted, so these tests never import its production modules.
The real HTML builder runs only against temporary synthetic artifacts.
"""
import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
STAFFER = ROOT / 'fec/tests/surfaces/test_make_staffer_network_html.py'
RUNNER = ROOT / 'fec/tests/run_tests.py'


@pytest.fixture
def staffer():
    spec = importlib.util.spec_from_file_location('staffer_html_fixture_suite', STAFFER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')
    return str(path)


@pytest.fixture
def artifacts(tmp_path, monkeypatch, staffer):
    # The ambient root is a stand-in for live data. A mixed-generation default
    # clients file must not contaminate a fixture that omits clients explicitly.
    ambient = tmp_path / 'ambient-root'
    clients = staffer.clients_fixture()
    clients['meta']['generation'] = 'foreign-generation-negative-control'
    foreign = write_json(ambient / 'data/engine/staffer_clients_section.json', clients)
    monkeypatch.setenv('MONEYBALL_ROOT', str(ambient))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    work = tmp_path / 'fixture-root'
    return {
        'data_path': write_json(work / 'network.json', staffer.fixture()),
        'offices_path': write_json(work / 'offices.json', staffer.offices_fixture()),
        'organisations_path': write_json(work / 'organisations.json', staffer.organisations_fixture()),
        'foreign': foreign,
        'work': work,
    }


@pytest.mark.parametrize('missing', ['offices', 'organisations'])
def test_missing_optional_fixture_cannot_consume_ambient_clients(staffer, artifacts, missing):
    paths = {key: artifacts[key] for key in ('offices_path', 'organisations_path')}
    paths[missing + '_path'] = str(artifacts['work'] / ('missing-' + missing + '.json'))
    output = artifacts['work'] / ('without-' + missing + '.html')
    code, text = staffer.build(artifacts['data_path'], str(output), **paths)
    assert code == 0, text
    page = output.read_text(encoding='utf-8')
    assert 'No clients section artifact was present' in page
    assert 'foreign-generation-negative-control' not in page
    assert ('ACME ADVOCACY' if missing == 'offices' else 'JUDICIARY') in page


def test_explicit_foreign_generation_still_fails_without_writing(staffer, artifacts):
    output = artifacts['work'] / 'rejected.html'
    code, text = staffer.build(
        artifacts['data_path'], str(output),
        offices_path=artifacts['offices_path'],
        organisations_path=artifacts['organisations_path'],
        clients_path=artifacts['foreign'])
    assert code == 1, text
    assert 'foreign-generation-negative-control' in text
    assert 'Nothing written.' in text
    assert not output.exists()


def staffer_footer(passed, failed):
    """Execute the actual suite's final print/return, not a duplicate formatter."""
    tree = ast.parse(STAFFER.read_text(encoding='utf-8'))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    main.body = main.body[-2:]
    namespace = {'P': passed, 'F': failed}
    exec(compile(ast.Module(body=[main], type_ignores=[]), str(STAFFER), 'exec'), namespace)
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = namespace['main']()
    return code, stream.getvalue()


def run_one_result(result, monkeypatch):
    """Run the shipped gate, counters and exit statement with one fake child."""
    tree = ast.parse(RUNNER.read_text(encoding='utf-8'))
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in ('ok', '_suite_verdict')]
    loops = [node for node in tree.body if isinstance(node, ast.For)
             and isinstance(node.iter, ast.Name) and node.iter.id == '_suite_specs']
    assert len(definitions) == 2 and len(loops) == 1
    monkeypatch.delenv('RUNTESTS_DUMP_DIR', raising=False)
    calls = []

    def bounded(argv, **kwargs):
        calls.append((argv, kwargs))
        return result

    namespace = {
        'P': 0, 'F': 0, '_re': re, 'os': os, 'sys': sys,
        '_suite_specs': [('test_make_staffer_network_html', ['synthetic-child'], None)],
        '_bounded': SimpleNamespace(run_bounded=bounded, KERNEL_SUITE_TIMEOUT_SECONDS=1),
    }
    body = definitions + loops + tree.body[-2:]
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream), pytest.raises(SystemExit) as caught:
        exec(compile(ast.Module(body=body, type_ignores=[]), str(RUNNER), 'exec'), namespace)
    assert len(calls) == 1
    return caught.value.code, namespace, stream.getvalue()


@pytest.mark.parametrize('passed,failed,child_code,expected', [
    (17, 0, 0, 0),
    (17, 1, 1, 1),
    (17, 1, 0, 1),  # a child cannot override its own failing footer with exit 0
    (17, 0, 1, 1),  # nor can a clean footer erase a later process failure
    (0, 0, 0, 1),   # empty execution is not a pass
])
def test_actual_staffer_footer_and_child_status_reach_kernel_exit(
        passed, failed, child_code, expected, monkeypatch):
    native_code, output = staffer_footer(passed, failed)
    assert native_code == (1 if failed else 0)
    result = SimpleNamespace(stdout=output, stderr='', returncode=child_code,
                             timed_out=False, timeout_note='')
    code, namespace, text = run_one_result(result, monkeypatch)
    assert code == expected, text
    assert (namespace['P'], namespace['F']) == ((1, 0) if expected == 0 else (0, 1))


@pytest.mark.parametrize('stdout,stderr,child_code,timed_out', [
    ('', '', 0, False),
    ('17 passed, failed', '', 0, False),
    ('  OK   initial check\nFAIL  absent offices :: got 1 want 0',
     'Traceback (most recent call last):\nFileNotFoundError: no-offices.html', 1, False),
    ('', 'synthetic timeout', 124, True),
])
def test_aborted_malformed_and_empty_results_fail_closed(
        stdout, stderr, child_code, timed_out, monkeypatch):
    result = SimpleNamespace(stdout=stdout, stderr=stderr, returncode=child_code,
                             timed_out=timed_out, timeout_note='synthetic timeout')
    code, namespace, text = run_one_result(result, monkeypatch)
    assert code == 1, text
    assert (namespace['P'], namespace['F']) == (0, 1)
    assert ('synthetic timeout' if timed_out else 'unrecognised verdict') in text
