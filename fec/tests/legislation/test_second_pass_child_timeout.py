"""Isolated child-boundary regressions; no producers or project data are loaded."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


FEC = Path(__file__).resolve().parents[2]
SOURCE = FEC / "legislation" / "run_legislation_second_pass_dependents.py"
SPEC = importlib.util.spec_from_file_location("timeout_fixture_bounded", FEC / "bounded_subprocess.py")
BOUNDED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOUNDED)


def boundary():
    tree = ast.parse(SOURCE.read_bytes(), filename=str(SOURCE))
    names = {"_child_timeout_seconds", "run_child"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    namespace = {
        "os": os, "subprocess": subprocess,
        "run_bounded": BOUNDED.run_bounded, "SecondPassError": RuntimeError,
        "sha256_bytes": lambda value: hashlib.sha256(value).hexdigest(),
        "canonical_bytes": lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
    }
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace


class ChildTimeoutTests(unittest.TestCase):
    def test_invalid_and_nonfinite_environment_uses_six_hour_default(self):
        functions = boundary()
        for value in ("", "invalid", "0", "-1", "nan", "inf", "+Infinity", "-inf", "1e999"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"LEGISLATION_CHILD_TIMEOUT_SECONDS": value}):
                self.assertEqual(functions["_child_timeout_seconds"](), 21600.0)

    def test_finite_positive_override_is_preserved(self):
        functions = boundary()
        for value in ("0.125", "23", "21601"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"LEGISLATION_CHILD_TIMEOUT_SECONDS": value}):
                self.assertEqual(functions["_child_timeout_seconds"](), float(value))

    def test_argv_environment_working_directory_and_receipt_preserved(self):
        functions = boundary()
        runner = mock.Mock(return_value=SimpleNamespace(returncode=0, timed_out=False, stdout="ok\n", stderr="warning\n"))
        functions["run_bounded"] = runner
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"LEGISLATION_CHILD_TIMEOUT_SECONDS": "42.5", "FIXTURE_INHERITED": "yes"}):
            root = Path(temporary)
            command = ("fixture-interpreter", "fixture-wrapper.py", "--phase", "second")
            receipt = functions["run_child"](root, command, label="fixture")
        args, kwargs = runner.call_args
        self.assertEqual(args, (list(command),))
        self.assertEqual(set(kwargs), {"cwd", "env", "timeout", "start_new_session"})
        self.assertEqual(kwargs["start_new_session"], os.name != "nt")
        self.assertEqual(kwargs["cwd"], root)
        self.assertEqual(kwargs["timeout"], 42.5)
        for key, value in {"MONEYBALL_ROOT": str(root), "LEGISLATION_SHADOW_MODE": "1", "LEGISLATION_SHADOW_RUN_DIR": str(root), "PYTHONDONTWRITEBYTECODE": "1", "FIXTURE_INHERITED": "yes"}.items():
            self.assertEqual(kwargs["env"][key], value)
        self.assertEqual(receipt, {"label": "fixture", "exit_code": 0, "argv_sha256": functions["sha256_bytes"](functions["canonical_bytes"](list(command))), "stdout_sha256": hashlib.sha256(b"ok\n").hexdigest(), "stderr_sha256": hashlib.sha256(b"warning\n").hexdigest()})

    def test_timeout_is_named_and_never_reported_as_success(self):
        functions = boundary()
        functions["run_bounded"] = mock.Mock(return_value=SimpleNamespace(returncode=124, timed_out=True, stdout="partial", stderr="private detail"))
        with mock.patch.dict(os.environ, {"LEGISLATION_CHILD_TIMEOUT_SECONDS": "3"}):
            with self.assertRaisesRegex(RuntimeError, r"^fixture timed out after 3.0s$"):
                functions["run_child"](Path("unused"), ["fixture"], label="fixture")

    def test_nonzero_exit_keeps_bounded_stderr_diagnostic(self):
        functions = boundary()
        functions["run_bounded"] = mock.Mock(return_value=SimpleNamespace(returncode=7, timed_out=False, stdout="ignored", stderr="z" * 1000))
        with self.assertRaises(RuntimeError) as caught:
            functions["run_child"](Path("unused"), ["fixture"], label="fixture")
        self.assertEqual(str(caught.exception), "fixture failed with exit 7: " + "z" * 800)

    def test_real_child_preserves_text_stream_hashes(self):
        functions = boundary()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"LEGISLATION_CHILD_TIMEOUT_SECONDS": "5"}):
            command = [sys.executable, "-B", "-c", "import os; os.write(1, b'a\\r\\nb\\xff'); os.write(2, b'c\\rd')"]
            receipt = functions["run_child"](Path(temporary), command, label="fixture")
        self.assertEqual(receipt["stdout_sha256"], hashlib.sha256("a\nb\ufffd".encode()).hexdigest())
        self.assertEqual(receipt["stderr_sha256"], hashlib.sha256(b"c\nd").hexdigest())

    def test_real_owned_child_stops_after_deadline(self):
        functions = boundary()
        children = []
        def tracked(*args, **kwargs):
            return BOUNDED.run_bounded(*args, **kwargs, on_start=children.append)
        functions["run_bounded"] = tracked
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"LEGISLATION_CHILD_TIMEOUT_SECONDS": "0.15"}):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "fixture timed out"):
                functions["run_child"](Path(temporary), [sys.executable, "-B", "-c", "import time; time.sleep(10)"], label="fixture")
            elapsed = time.monotonic() - started
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertLess(elapsed, 8.0)


if __name__ == "__main__":
    unittest.main()
