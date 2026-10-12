from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "fec" / "legislation" / "run_legislation_acceptance_service.py"
SPEC = importlib.util.spec_from_file_location("acceptance_service", MODULE_PATH)
assert SPEC and SPEC.loader
SERVICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVICE)


class AcceptanceServiceTests(unittest.TestCase):
    def test_modes_are_explicit_and_full_is_never_default(self) -> None:
        with self.assertRaises(SystemExit):
            SERVICE.parse_args([])
        self.assertEqual(SERVICE.mode_from_args(SERVICE.parse_args(["--plan-only"])), "plan")
        self.assertEqual(
            SERVICE.mode_from_args(SERVICE.parse_args(["--canary-only"])), "canary"
        )
        self.assertEqual(SERVICE.mode_from_args(SERVICE.parse_args(["--full"])), "full")

    def test_stage_plan_keeps_plan_only_offline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / "tool.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            input_dir = base / "input"
            input_dir.mkdir()
            specs = SERVICE.stage_specs(
                mode="plan",
                attempt_tag="test-0001",
                python=Path(sys.executable),
                control_script=script,
                collector_script=script,
                archive_audit_script=script,
                govinfo_input=input_dir,
                govinfo_out=base / "govinfo",
                html_run=base / "html",
                discovery=base / "discovery.ndjson",
                control_output=base / "control.json",
                archive_audit_out=base / "rehash",
                canary_size=2,
            )
            names = [row.name for row in specs]
            self.assertEqual(
                names,
                [
                    "baseline_control_plane_refresh",
                    "govinfo_collector_cli_contract",
                    "govinfo_plan",
                    "full_text_archive_rehash_audit",
                ],
            )
            all_arguments = [argument for row in specs for argument in row.command]
            self.assertIn("--plan-only", all_arguments)
            self.assertNotIn("--canary", all_arguments)
            self.assertNotIn("--full", all_arguments)

    def test_active_lock_fails_and_stale_lock_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            active = {
                "schema": "legislation-acceptance-service-lock-v1",
                "owner_token": "active",
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "child_pid": None,
            }
            SERVICE.atomic_write(state_dir / "service.lock", SERVICE.canonical_bytes(active))
            lock = SERVICE.ServiceLock(
                state_dir,
                {
                    "schema": "legislation-acceptance-service-lock-v1",
                    "owner_token": "new",
                    "hostname": socket.gethostname(),
                    "pid": os.getpid(),
                    "child_pid": None,
                },
            )
            with self.assertRaises(SERVICE.ServiceError):
                lock.acquire()

            (state_dir / "service.lock").unlink()
            stale = {**active, "pid": 999_999_999, "owner_token": "stale"}
            SERVICE.atomic_write(state_dir / "service.lock", SERVICE.canonical_bytes(stale))
            recovered = SERVICE.ServiceLock(
                state_dir,
                {
                    "schema": "legislation-acceptance-service-lock-v1",
                    "owner_token": "recovered",
                    "hostname": socket.gethostname(),
                    "pid": os.getpid(),
                    "child_pid": None,
                },
            )
            recovered.acquire()
            try:
                self.assertTrue(list((state_dir / "stale_locks").glob("*.json")))
            finally:
                recovered.release()

    def test_hash_current_stage_is_skipped_and_tamper_forces_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state_dir = base / "state"
            output = base / "output.json"
            tool = base / "tool.py"
            tool.write_text(
                "import json, pathlib, sys\n"
                "p=pathlib.Path(sys.argv[1]); "
                "n=(json.loads(p.read_text())['n'] if p.exists() else 0)+1; "
                "p.write_text(json.dumps({'n':n}))\n",
                encoding="utf-8",
            )
            state = {
                "service_id": "test-service",
                "stages": {},
                "skipped_stage_count": 0,
            }
            state_dir.mkdir()
            SERVICE.atomic_write(
                state_dir / "state.json", SERVICE.canonical_bytes(state)
            )
            owner = {
                "schema": "legislation-acceptance-service-lock-v1",
                "owner_token": "owner",
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "child_pid": None,
            }
            spec = SERVICE.StageSpec(
                "test",
                [sys.executable, str(tool), str(output)],
                [tool],
                [output],
                lambda: SERVICE.read_json(output),
            )
            with SERVICE.ServiceLock(state_dir, owner) as lock:
                self.assertEqual(
                    SERVICE.execute_stage(state_dir, state, spec, lock), "completed"
                )
                self.assertEqual(
                    SERVICE.execute_stage(state_dir, state, spec, lock), "skipped"
                )
                self.assertEqual(json.loads(output.read_text())["n"], 1)
                output.write_text('{"n":999}\n', encoding="utf-8")
                self.assertEqual(
                    SERVICE.execute_stage(state_dir, state, spec, lock), "completed"
                )
                self.assertEqual(json.loads(output.read_text())["n"], 1000)
                self.assertEqual(len(state["stages"]["test"]["attempts"]), 2)

    def test_failed_stage_records_exit_code_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state_dir = base / "state"
            state_dir.mkdir()
            tool = base / "fail.py"
            tool.write_text(
                "import sys\nprint('visible stdout')\n"
                "print('visible stderr', file=sys.stderr)\nsys.exit(7)\n",
                encoding="utf-8",
            )
            state = {
                "service_id": "test-service",
                "stages": {},
                "skipped_stage_count": 0,
            }
            SERVICE.atomic_write(
                state_dir / "state.json", SERVICE.canonical_bytes(state)
            )
            owner = {
                "schema": "legislation-acceptance-service-lock-v1",
                "owner_token": "owner",
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "child_pid": None,
            }
            spec = SERVICE.StageSpec(
                "failure",
                [sys.executable, str(tool)],
                [tool],
                [base / "missing.json"],
                lambda: None,
            )
            with SERVICE.ServiceLock(state_dir, owner) as lock:
                with self.assertRaises(SERVICE.ServiceError):
                    SERVICE.execute_stage(state_dir, state, spec, lock)
            receipt_path = Path(state["stages"]["failure"]["receipt"])
            receipt = SERVICE.read_json(receipt_path)
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["exit_code"], 7)
            self.assertEqual(receipt["retry_waits"], 0)
            self.assertRegex(receipt["stdout"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(receipt["stderr"]["sha256"], r"^[0-9a-f]{64}$")
            for key, expected in SERVICE.FORBIDDEN_POLICY.items():
                self.assertIs(receipt[key], expected)

    def test_archive_rehash_gate_rejects_provisional_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manifest = base / "manifest.json"
            provisional = {
                "status": "PASS_RECEIPT_LEVEL_NO_UNRESOLVED_FAILURES",
                "accepted_archive_body_rehash_performed": False,
                "recovery_is_provisional_until_archive_rehash": True,
                "every_dispatched_key_terminally_accounted": True,
                "invalid_accepted_receipt_count": 0,
                "unaccounted_dispatch_count": 0,
                "malformed_batch_receipt_count": 0,
                "unknown_failure_keys": [],
                "unknown_batch_keys": [],
            }
            manifest.write_text(json.dumps(provisional), encoding="utf-8")
            pointer = base / "current.json"
            pointer.write_text(
                json.dumps(
                    {
                        "manifest": {
                            "path": str(manifest),
                            "sha256": SERVICE.sha256_file(manifest),
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SERVICE.ServiceError):
                SERVICE.validate_archive_rehash(pointer)


if __name__ == "__main__":
    unittest.main()
