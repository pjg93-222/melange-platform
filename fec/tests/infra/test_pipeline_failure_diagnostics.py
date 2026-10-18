"""Failure evidence contracts, with only bounded scratch children and AST-lifted controller code."""
import argparse
import ast
from contextlib import nullcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

FEC = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('failure_diagnostic_bounded_helper', FEC / 'bounded_subprocess.py')
bounded = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bounded)


@pytest.fixture
def controller(tmp_path, monkeypatch):
    # Import no producer or live controller globals. Exercise the actual main and capture
    # functions with all writer paths, locks, events and dependency checks bound to scratch.
    tree = ast.parse((FEC / 'run_pipeline.py').read_text(encoding='utf-8'))
    names = {'start_index', '_step_failure_metadata', '_record_step_failure', 'main'}
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in definitions} == names
    child = tmp_path / 'probe.py'
    calls = []
    events = []

    def launch(argv, **kwargs):
        result = bounded.run_bounded(argv, **kwargs)
        calls.append((argv, result))
        return result

    scope = {
        'argparse': argparse, 'hashlib': hashlib, 'json': json, 'os': os,
        'sys': sys, 'time': time, 'uuid': uuid,
        'bounded_subprocess': SimpleNamespace(CHAIN_STEP_TIMEOUT_SECONDS=5),
        'run_bounded': launch, 'MB_ROOT': str(tmp_path),
        'CHAIN': [('probe.py', [])], 'OPTIONAL_INPUTS': {},
        'TOLERANT_STEPS': set(), 'LAST_GOOD': {}, 'LAST_GOOD_RECEIPT': {},
        'TOLERATED_RECEIPT': 'tolerated.json', '_GB_RUN': 'test', '_GB_CORR': 'test',
        '_producer_path': lambda name: str(child), 'preflight_dependencies': lambda: None,
        'pipeline_run_lock': nullcontext,
        '_gb': lambda *args, **kwargs: events.append((args, kwargs)),
    }
    monkeypatch.syspath_prepend(str(FEC))  # The failure-output helper is stdlib-only.
    # main() opens a chain transaction on MB_ROOT (chain_transaction.py). Lift its 30 GB free-disk floor, which is the host's
    # business and not this suite's, and stage next to the root so the root listing assertions below stay exact.
    monkeypatch.setenv('MELANGE_CHAIN_TXN_MIN_FREE_GB', '0')
    monkeypatch.setenv('MELANGE_CHAIN_TXN_DIR', str(tmp_path.parent / (tmp_path.name + '-chain-txn')))
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(FEC / 'run_pipeline.py'), 'exec'), scope)
    return SimpleNamespace(scope=scope, child=child, calls=calls, events=events, root=tmp_path)


def captures(path):
    return sorted(path.glob('failure-*/metadata.json'))


def test_real_bounded_import_failure_keeps_full_streams_pid_and_argv(controller, capsys):
    prefix = 'OUT_START_' + 'a' * 12000 + '_OUT_END'
    error = 'ERR_START_' + 'b' * 15000 + '_ERR_END'
    controller.child.write_text(
        "import os,sys\nprint('CHILD_PID='+str(os.getpid()),flush=True)\n"
        f"print({prefix!r},flush=True)\nprint({error!r},file=sys.stderr,flush=True)\n"
        'import _moneyball_planted_missing_dependency_20261002\n', encoding='utf-8')
    destination = controller.root / 'diagnostics'
    code = controller.scope['main'](['--diagnostics-dir', str(destination)])
    console = capsys.readouterr().out
    assert code == 1 and len(controller.calls) == 1
    argv, child = controller.calls[0]
    record_path, = captures(destination)
    record = json.loads(record_path.read_text())
    stdout = (record_path.parent / 'stdout.txt').read_text(encoding='utf-8')
    stderr = (record_path.parent / 'stderr.txt').read_text(encoding='utf-8')
    assert stdout == child.stdout and stderr == child.stderr
    assert prefix in stdout and error in stderr
    assert 'ModuleNotFoundError' in stderr and '_moneyball_planted_missing_dependency_20261002' in stderr
    assert record['pid'] == child.pid == int(stdout.splitlines()[0].split('=')[1])
    assert record['argv'] == argv == [sys.executable, str(controller.child)]
    assert record['returncode'] == record['native_returncode'] == 1
    assert record['returncode_u32_hex'] == '0x00000001'
    assert record['timeout_seconds'] == 5 and record['timed_out'] is False
    assert f'child pid={child.pid} returncode=1' in console
    assert 'PIPELINE COMPLETE' not in console
    for name, text in [('stdout', stdout), ('stderr', stderr)]:
        assert record['streams'][name]['bytes'] == len(text.encode('utf-8'))
        assert record['streams'][name]['sha256'] == hashlib.sha256(text.encode('utf-8')).hexdigest()


def test_real_blank_child_still_reports_exact_result(controller, capsys):
    controller.child.write_text('import os\nos._exit(7)\n', encoding='utf-8')
    destination = controller.root / 'blank'
    assert controller.scope['main'](['--diagnostics-dir', str(destination)]) == 1
    result = controller.calls[0][1]
    assert result.stdout == result.stderr == ''
    assert f'child pid={result.pid} returncode=7 (0x00000007)' in capsys.readouterr().out
    path, = captures(destination)
    record = json.loads(path.read_text())
    assert record['pid'] == result.pid and record['native_returncode'] == 7
    assert (path.parent / 'stdout.txt').read_bytes() == b''
    assert (path.parent / 'stderr.txt').read_bytes() == b''


@pytest.mark.parametrize('native_code', [0xC000070A, -1073740022])
def test_native_status_control_retains_signed_value_and_u32(controller, capsys, native_code):
    result = bounded.BoundedResult(native_code, '', '', False, 45678, 0.2, '')
    controller.scope['run_bounded'] = lambda *args, **kwargs: result
    destination = controller.root / 'native'
    assert controller.scope['main'](['--diagnostics-dir', str(destination)]) == 1
    output = capsys.readouterr().out
    assert f'pid=45678 returncode={native_code} (0xC000070A)' in output
    path, = captures(destination)
    record = json.loads(path.read_text())
    assert record['returncode'] == record['native_returncode'] == native_code
    assert record['returncode_u32_hex'] == '0xC000070A'


def test_timeout_sentinel_is_not_mislabelled_native_exit(controller, capsys):
    result = bounded.BoundedResult(124, '', 'TIMED OUT', True, 56789, 5.1, 'TIMED OUT after5s')
    controller.scope['run_bounded'] = lambda *args, **kwargs: result
    destination = controller.root / 'timeout'
    assert controller.scope['main'](['--diagnostics-dir', str(destination)]) == 1
    assert 'timeout sentinel; native exit unavailable' in capsys.readouterr().out
    path, = captures(destination)
    record = json.loads(path.read_text())
    assert record['returncode'] == 124 and record['native_returncode'] is None
    assert record['timed_out'] is True and record['timeout_note'] == 'TIMED OUT after5s'


def test_unwritable_capture_does_not_mask_original_failure(controller, capsys):
    controller.child.write_text('raise RuntimeError("original planted failure")\n', encoding='utf-8')
    destination = controller.root / 'not-a-directory'
    destination.write_text('preserved')
    assert controller.scope['main'](['--diagnostics-dir', str(destination)]) == 1
    output = capsys.readouterr().out
    assert 'failure diagnostics not written' in output
    assert 'original planted failure' in output and 'returncode=1' in output
    assert destination.read_text() == 'preserved' and len(controller.calls) == 1


def test_metadata_write_failure_preserves_failure_and_partial_streams(controller, capsys):
    result = bounded.BoundedResult(9, 'complete stdout', 'complete stderr', False, 90, 0.1, '')
    controller.scope['run_bounded'] = lambda *args, **kwargs: result
    original_open = open

    def fail_metadata(path, *args, **kwargs):
        if Path(path).name == 'metadata.json':
            raise PermissionError('planted metadata write failure')
        return original_open(path, *args, **kwargs)

    controller.scope['open'] = fail_metadata
    destination = controller.root / 'partial'
    assert controller.scope['main'](['--diagnostics-dir', str(destination)]) == 1
    assert 'planted metadata write failure' in capsys.readouterr().out
    assert list(destination.glob('failure-*/stdout.txt'))[0].read_text() == 'complete stdout'
    assert list(destination.glob('failure-*/stderr.txt'))[0].read_text() == 'complete stderr'


def test_capture_collision_never_overwrites_previous_evidence(controller, capsys):
    result = bounded.BoundedResult(3, 'first evidence', '', False, 30, 0.1, '')
    controller.scope['run_bounded'] = lambda *args, **kwargs: result
    controller.scope['uuid'] = SimpleNamespace(uuid4=lambda: SimpleNamespace(hex='planted-collision'))
    destination = controller.root / 'collision'
    args = ['--diagnostics-dir', str(destination)]
    assert controller.scope['main'](args) == 1
    previous = {path.name: path.read_bytes() for path in (destination / 'failure-planted-collision').iterdir()}
    result.stdout = 'second must not overwrite'
    assert controller.scope['main'](args) == 1
    assert 'failure diagnostics not written' in capsys.readouterr().out
    assert {path.name: path.read_bytes() for path in (destination / 'failure-planted-collision').iterdir()} == previous


def test_repeated_failures_get_separate_complete_captures(controller):
    controller.scope['run_bounded'] = lambda *args, **kwargs: bounded.BoundedResult(2, 'refusal', '', False, 20, 0.1, '')
    destination = controller.root / 'distinct'
    for _ in range(2):
        assert controller.scope['main'](['--diagnostics-dir', str(destination)]) == 1
    assert len(captures(destination)) == 2


def test_success_does_not_create_diagnostic_artifacts_or_failure_line(controller, capsys):
    controller.child.write_text('print("success control")\n', encoding='utf-8')
    destination = controller.root / 'successful'
    assert controller.scope['main'](['--diagnostics-dir', str(destination)]) == 0
    output = capsys.readouterr().out
    assert 'PIPELINE COMPLETE' in output and 'success control' in output
    assert 'child pid=' not in output and not destination.exists()


def test_expected_absence_skip_is_unchanged(controller, capsys):
    controller.scope['OPTIONAL_INPUTS'] = {'probe.py': 'deliberately-absent.json'}
    destination = controller.root / 'skipped'
    assert controller.scope['main'](['--diagnostics-dir', str(destination)]) == 0
    output = capsys.readouterr().out
    assert 'skip:' in output and 'PIPELINE COMPLETE' in output
    assert not controller.calls and not destination.exists()


def test_no_option_reports_failure_without_writing_capture(controller, capsys):
    controller.child.write_text('raise RuntimeError("no-option failure")\n', encoding='utf-8')
    assert controller.scope['main']([]) == 1
    assert 'returncode=1' in capsys.readouterr().out
    assert sorted(path.name for path in controller.root.iterdir()) == ['probe.py']
