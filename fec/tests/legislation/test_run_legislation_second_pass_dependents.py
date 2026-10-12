from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


FEC = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_legislation_second_pass_dependents",
    FEC / "legislation" / "run_legislation_second_pass_dependents.py",
)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)

GENERATION = "a" * 64


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def make_fixture(run_dir: Path) -> dict[str, object]:
    accepted_manifest = write_json(
        run_dir / "inputs" / "accepted_generation" / "accepted_manifest.json",
        {
            "schema": "accepted-fixture-v1",
            "status": "PASS",
            "canonical_ledger_sha256": GENERATION,
        },
    )
    write_json(
        run_dir / MOD.INPUT_MATERIALIZATION,
        {
            "schema": "legislation-shadow-input-materialization-v1",
            "records": [
                {
                    "source_group": "accepted_generation",
                    "logical_name": "manifest",
                    "source_sha256": digest(accepted_manifest),
                    "materialized_path": accepted_manifest.relative_to(
                        run_dir
                    ).as_posix(),
                    "materialized_sha256": digest(accepted_manifest),
                }
            ],
        },
    )

    artifacts: dict[str, Path] = {}
    for logical, contract in MOD.network.REQUIRED_ARTIFACTS.items():
        path = run_dir / contract.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            write_json(path, {"fixture": logical})
        else:
            path.write_bytes(f"{logical}-fixture\n".encode())
        artifacts[logical] = path
    write_json(
        run_dir / "data/members/lep_criterion.json",
        {"per_member": {"Fixture": {}}},
    )

    say_do = artifacts["say_do_first_pass"]
    write_json(
        say_do,
        {
            "members": {
                "Fixture": {
                    "quadrant": "aligned",
                    "say_n": 0,
                    "sponsored_cu": 0,
                    "advanced_cu": 0,
                    "do_score": 0,
                    "say_z": 0,
                    "do_z": 0,
                    "gap": 0,
                    "adv_rate": None,
                }
            },
            "method": "fixture",
        },
    )
    say_do_receipt = write_json(
        run_dir / MOD.SAY_DO_SECOND_RECEIPT,
        {
            "schema": MOD.SAY_DO_SCHEMA,
            "status": "PASS",
            "pass": "second",
            "generation_id": GENERATION,
            "staged_only": True,
            "production_or_mainframe_written": False,
            "expected_input_sha256": {
                "ranks_python.json": digest(
                    artifacts["ranks"]
                )
            },
            "declared_output": {
                "path": "say_do_gap.json",
                "sha256": digest(say_do),
                "size": say_do.stat().st_size,
            },
        },
    )

    workbook = artifacts["legislative_workbook"]
    ranks = artifacts["ranks"]
    write_json(
        run_dir / "pillars_ranks.receipt.json",
        {
            "schema": "legislation-pillars-ranks-receipt-v1",
            "status": "PASS",
            "generation_id": GENERATION,
            "outputs": {
                workbook.name: {
                    "path": workbook.name,
                    "sha256": digest(workbook),
                },
                ranks.name: {
                    "path": ranks.name,
                    "sha256": digest(ranks),
                },
            },
        },
    )
    bipartisan = artifacts["bipartisan"]
    write_json(
        run_dir / "base_bipartisan_manifest.json",
        {
            "schema": "legislation-base-bipartisan-materialization-v1",
            "status": "PASS",
            # Deliberately a content-generation id.  The coordinator must
            # adapt it to the accepted generation using the step ledger.
            "generation_id": "b" * 64,
            "inputs": {"accepted_ledger_sha256": GENERATION},
            "outputs": {
                bipartisan.name: {
                    "path": bipartisan.name,
                    "sha256": digest(bipartisan),
                }
            },
        },
    )

    step_outputs: dict[str, dict[str, dict[str, object]]] = {}
    for logical, contract in MOD.network.REQUIRED_ARTIFACTS.items():
        if not contract.generation_bound:
            continue
        step = MOD.ARTIFACT_SOURCE_STEPS[contract.path]
        step_outputs.setdefault(step, {})[contract.path] = {
            "sha256": digest(artifacts[logical]),
            "size": artifacts[logical].stat().st_size,
        }
    step_outputs["recompute_say_do_second_pass"][
        MOD.SAY_DO_SECOND_RECEIPT.as_posix()
    ] = {
        "sha256": digest(say_do_receipt),
        "size": say_do_receipt.stat().st_size,
    }
    ledger = write_json(
        run_dir / MOD.STEP_LEDGER,
        {
            "schema": MOD.STEP_LEDGER_SCHEMA,
            "status": "RUNNING",
            "staged_only": True,
            "production_written": False,
            "generation_id": GENERATION,
            "accepted_generation": {
                "manifest_sha256": digest(accepted_manifest),
                "canonical_ledger_sha256": GENERATION,
            },
            "completed_steps": [
                {"name": name, "outputs": outputs}
                for name, outputs in step_outputs.items()
            ],
        },
    )
    return {
        "artifacts": artifacts,
        "ledger": ledger,
        "say_do": say_do,
        "say_do_receipt": say_do_receipt,
        "accepted_manifest": accepted_manifest,
    }


def child_success(run_dir: Path, calls: list[str]):
    def run(
        command,
        *,
        cwd,
        env,
        text,
        encoding,
        errors,
        capture_output,
        check,
    ):
        del env, text, encoding, errors, capture_output, check
        assert Path(cwd) == run_dir
        command = [str(item) for item in command]
        if "run_legislation_network_dependents.py" in command[1]:
            calls.append("network")
            for name in MOD.NETWORK_FINAL_OUTPUTS:
                write_json(run_dir / name, {"producer": "network", "name": name})
            outputs = {
                name: {
                    "path": name,
                    "size": (run_dir / name).stat().st_size,
                    "sha256": digest(run_dir / name),
                }
                for name in MOD.NETWORK_FINAL_OUTPUTS
            }
            write_json(
                run_dir / MOD.network_receipt_relative(),
                {
                    "schema": MOD.network.SCHEMA,
                    "status": "PASS",
                    "generation_id": GENERATION,
                    "declared_outputs": outputs,
                },
            )
        elif "run_legislation_dashboard_member_desk.py" in command[1]:
            calls.append("dashboard")
            for name in MOD.DASHBOARD_FINAL_OUTPUTS:
                path = run_dir / name
                if path.suffix == ".json":
                    write_json(
                        path, {"producer": "dashboard", "name": name}
                    )
                else:
                    path.write_text(
                        f"<html>{name}</html>", encoding="utf-8"
                    )
            outputs = {
                name: {
                    "path": name,
                    "size": (run_dir / name).stat().st_size,
                    "sha256": digest(run_dir / name),
                }
                for name in MOD.DASHBOARD_FINAL_OUTPUTS
            }
            say_do_sha = digest(run_dir / "say_do_gap.json")
            say_do_binding = {
                "schema": MOD.SAY_DO_SCHEMA,
                "pass": "second",
                "declared_output": {
                    "path": "say_do_gap.json",
                    "sha256": say_do_sha,
                },
                "expected_input_sha256": {},
            }
            write_json(
                run_dir / MOD.dashboard.SECOND_RECEIPT_MIRROR,
                {
                    "schema": MOD.dashboard.SCHEMA,
                    "status": "PASS",
                    "phase": "second",
                    "generation_id": "d" * 64,
                    "generation_basis_sha256": "d" * 64,
                    "network_prerequisite": {
                        "generation_id": GENERATION
                    },
                    "say_do_second_prerequisite": say_do_binding,
                    "generation_basis": {
                        "say_do_second_generation": say_do_binding
                    },
                    "dynamic_inputs": {
                        "say_do_gap.json": {
                            "path": "say_do_gap.json",
                            "sha256": say_do_sha,
                        }
                    },
                    "declared_outputs": outputs,
                },
            )
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    return run


def fixed_point_runner_success(run_dir: Path):
    def run(probe_dir, producer, expected_inputs):
        del producer, expected_inputs
        source = run_dir / "say_do_gap.json"
        output = probe_dir / "say_do_gap.json"
        output.write_bytes(source.read_bytes())
        return (
            {
                "command": ["fixture-say-do"],
                "command_sha256": "f" * 64,
                "repeat_output_sha256": [digest(output), digest(output)],
            },
            {
                "run_1_stdout_sha256": "1" * 64,
                "run_1_stderr_sha256": "2" * 64,
                "run_2_stdout_sha256": "3" * 64,
                "run_2_stderr_sha256": "4" * 64,
            },
        )

    return run


def shadow_environment(run_dir: Path):
    return mock.patch.dict(
        os.environ,
        {
            "LEGISLATION_SHADOW_MODE": "1",
            "LEGISLATION_SHADOW_RUN_DIR": str(run_dir),
            "MONEYBALL_ROOT": str(run_dir),
        },
    )


class SecondPassDependentsTests(unittest.TestCase):
    def test_final_chain_is_ordered_generation_bound_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            fixture = make_fixture(run_dir)
            calls: list[str] = []
            with shadow_environment(run_dir):
                with mock.patch.object(
                    MOD.subprocess,
                    "run",
                    side_effect=child_success(run_dir, calls),
                ):
                    with mock.patch.object(
                        MOD.say_do_runner,
                        "run_producer_twice",
                        side_effect=fixed_point_runner_success(run_dir),
                    ):
                        first = MOD.run_second_pass_dependents(run_dir)
            self.assertEqual(calls, ["network", "dashboard"])
            self.assertEqual(first["status"], "PASS")
            self.assertEqual(first["generation_id"], GENERATION)
            self.assertEqual(
                tuple(first["declared_outputs"]), MOD.FINAL_OUTPUTS
            )
            self.assertFalse(first["production_written"])
            self.assertFalse(first["mainframe_written"])
            self.assertFalse(first["algorithms_changed"])
            self.assertTrue(first["no_one_generation_say_do_lag"])
            self.assertTrue(
                first["dashboard_second_pass_binding"]["binding_complete"]
            )
            self.assertTrue(
                first["say_do_fixed_point_proof"]["proof"]["parity"]
            )
            self.assertEqual(
                digest(fixture["accepted_manifest"]),
                first["accepted_input_manifest"]["sha256"],
            )

            prerequisites = json.loads(
                (
                    run_dir / MOD.NETWORK_SECOND_PREREQUISITES
                ).read_text(encoding="utf-8")
            )
            base_receipt = prerequisites["upstream_receipts"][
                "base_bipartisan"
            ]
            base_binding = json.loads(
                (run_dir / base_receipt["path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                base_binding["receipt_role"],
                "accepted-generation-binding-adapter",
            )
            self.assertEqual(base_binding["generation_id"], GENERATION)

            before = (run_dir / MOD.FINAL_RECEIPT).read_bytes()
            with shadow_environment(run_dir):
                with mock.patch.object(
                    MOD.subprocess,
                    "run",
                    side_effect=AssertionError("must reuse receipt"),
                ):
                    second = MOD.run_second_pass_dependents(run_dir)
            self.assertTrue(second["_idempotent_reuse"])
            self.assertEqual(
                before, (run_dir / MOD.FINAL_RECEIPT).read_bytes()
            )

    def test_tampered_second_pass_output_fails_before_children(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            fixture = make_fixture(run_dir)
            fixture["say_do"].write_text("tampered\n", encoding="utf-8")
            with shadow_environment(run_dir):
                with mock.patch.object(
                    MOD.subprocess,
                    "run",
                    side_effect=AssertionError("child must not run"),
                ):
                    with self.assertRaisesRegex(
                        MOD.SecondPassError, "output hash differs"
                    ):
                        MOD.run_second_pass_dependents(run_dir)
            self.assertFalse((run_dir / MOD.FINAL_RECEIPT).exists())

    def test_missing_exact_step_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            fixture = make_fixture(run_dir)
            ledger = json.loads(
                fixture["ledger"].read_text(encoding="utf-8")
            )
            for row in ledger["completed_steps"]:
                if row["name"] == "recompute_say_do_second_pass":
                    row["outputs"].pop(
                        MOD.SAY_DO_SECOND_RECEIPT.as_posix()
                    )
            write_json(fixture["ledger"], ledger)
            with shadow_environment(run_dir):
                with self.assertRaisesRegex(
                    MOD.SecondPassError, "exact missing upstream receipt"
                ):
                    MOD.run_second_pass_dependents(run_dir)

    def test_child_failure_never_seals_final_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            make_fixture(run_dir)

            def failed(*args, **kwargs):
                del kwargs
                return subprocess.CompletedProcess(
                    args[0], 7, "", "fixture failure"
                )

            with shadow_environment(run_dir):
                with mock.patch.object(
                    MOD.subprocess, "run", side_effect=failed
                ):
                    with self.assertRaisesRegex(
                        MOD.SecondPassError, "network.*failed"
                    ):
                        MOD.run_second_pass_dependents(run_dir)
            self.assertFalse((run_dir / MOD.FINAL_RECEIPT).exists())

    def test_dashboard_mere_generation_mention_is_not_a_say_do_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            fixture = make_fixture(run_dir)
            write_json(
                run_dir / MOD.dashboard.SECOND_RECEIPT_MIRROR,
                {
                    "schema": MOD.dashboard.SCHEMA,
                    "status": "PASS",
                    "phase": "second",
                    "network_prerequisite": {
                        "generation_id": GENERATION
                    },
                    # The old loose child check could accept a receipt that
                    # merely contained the accepted generation somewhere.
                    "note": GENERATION,
                    "declared_outputs": {},
                },
            )
            with self.assertRaisesRegex(
                MOD.SecondPassError, "not bound to the exact second say/do"
            ):
                MOD.validate_dashboard_second_binding(
                    run_dir,
                    generation_id=GENERATION,
                    say_do={
                        "output": {
                            "sha256": digest(fixture["say_do"])
                        }
                    },
                )

    def test_fixed_point_mismatch_blocks_without_overwriting_second_say_do(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            fixture = make_fixture(run_dir)
            second_before = fixture["say_do"].read_bytes()
            write_json(
                run_dir / "member_desk_bundle.json",
                {"members": [{"name": "Final"}]},
            )
            write_json(
                run_dir / "data/members/lep_criterion.json",
                {"per_member": {"Final": {}}},
            )

            def changed(probe_dir, producer, expected_inputs):
                del producer, expected_inputs
                write_json(
                    probe_dir / "say_do_gap.json",
                    {"members": {"Final": {"gap": 1}}, "method": "changed"},
                )
                return (
                    {
                        "command": ["fixture-changed"],
                        "command_sha256": "e" * 64,
                        "repeat_output_sha256": [],
                    },
                    {},
                )

            with mock.patch.object(
                MOD.say_do_runner,
                "run_producer_twice",
                side_effect=changed,
            ):
                with self.assertRaisesRegex(
                    MOD.SecondPassError, "one-generation lag"
                ):
                    MOD.prove_say_do_fixed_point(
                        run_dir,
                        generation_id=GENERATION,
                        say_do={
                            "output": {
                                "sha256": digest(fixture["say_do"])
                            }
                        },
                    )
            self.assertEqual(second_before, fixture["say_do"].read_bytes())
            proof = json.loads(
                (
                    run_dir / MOD.SAY_DO_FIXED_POINT_RECEIPT
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(proof["status"], "BLOCKED")
            self.assertFalse(proof["parity"])
            self.assertFalse((run_dir / MOD.FINAL_RECEIPT).exists())

    def test_workspace_root_and_mixed_accepted_generation_are_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            MOD.SecondPassError, "workspace root"
        ):
            MOD.validate_run_dir(MOD.REPO_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            fixture = make_fixture(run_dir)
            accepted = json.loads(
                fixture["accepted_manifest"].read_text(encoding="utf-8")
            )
            accepted["canonical_ledger_sha256"] = "c" * 64
            write_json(fixture["accepted_manifest"], accepted)
            with shadow_environment(run_dir):
                with self.assertRaisesRegex(
                    MOD.SecondPassError,
                    "accepted input manifest is missing or ambiguous",
                ):
                    MOD.run_second_pass_dependents(run_dir)


if __name__ == "__main__":
    unittest.main()
