from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


FEC = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FIRST = load("run_legislation_say_do_first_pass", FEC / "legislation" / "run_legislation_say_do_first_pass.py")
SECOND = load(
    "run_legislation_say_do_second_pass",
    FEC / "legislation" / "run_legislation_say_do_second_pass.py",
)


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def desk(variant: int = 0) -> dict[str, object]:
    return {
        "members": [
            {
                "name": "Alice",
                "chamber": "house",
                "articles": [{"pol": 1}] * (1 + variant),
                "bills": [
                    {"role": "sponsor", "action": "Passed House"},
                    {"role": "cosponsor", "action": "Introduced"},
                ],
            },
            {
                "name": "Bob",
                "chamber": "house",
                "articles": [],
                "bills": [{"role": "original", "action": "Became Law"}],
            },
            {
                "name": "Carol",
                "chamber": "senate",
                "articles": [{"pol": -1}],
                "bills": [],
            },
        ]
    }


def prepare_first(root: Path) -> None:
    write_json(root / "member_desk_bundle.json", desk())
    write_json(
        root / "data/members/lep_criterion.json",
        {
            "per_member": {
                name: {"followthrough": 0.5, "lesclassic": 0.4}
                for name in ("Alice", "Bob", "Carol")
            }
        },
    )
    write_json(root / "category_stance.json", {"members": {}})
    write_step_ledger(root, through="recompute_category_stance")


def write_step_ledger(root: Path, *, through: str) -> None:
    order = [
        "materialize_archive_and_legislative_workbook",
        "materialize_rollcall_aggregate",
        "materialize_base_network_and_prebehavior_bipartisan",
        "recompute_behavior_panel",
        "recompute_behavioral_dispositions",
        "recompute_cooperation",
        "recompute_receptivity",
        "recompute_augmented_network",
        "recompute_category_stance",
        "recompute_say_do",
        "recompute_legislative_raw_normalized_sublayer",
        "recompute_pillars_and_ranks",
        "recompute_coalitions_flips_budget_optimizer_allocation",
        "recompute_dashboard_and_member_desk",
    ]
    selected = order[: order.index(through) + 1]
    write_json(
        root / "intermediate" / "shadow_step_receipts.json",
        {
            "schema": FIRST.STEP_LEDGER_SCHEMA,
            "status": "RUNNING",
            "staged_only": True,
            "production_written": False,
            "generation_id": "a" * 64,
            "completed_steps": [{"name": name} for name in selected],
        },
    )


def shadow_env(root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "MONEYBALL_ROOT": str(root),
        "LEGISLATION_SHADOW_MODE": "1",
        "LEGISLATION_SHADOW_RUN_DIR": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def run_script(script: Path, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=root,
        env=shadow_env(root),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


class SayDoPassTests(unittest.TestCase):
    def test_existing_producer_formula_contract(self) -> None:
        result = FIRST.audit_producer_contract()
        self.assertEqual(result["sha256"], FIRST.sha256_file(FIRST.PRODUCER))

    def test_first_pass_is_hash_copied_and_repeat_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_first(root)
            first = run_script(FEC / "legislation" / "run_legislation_say_do_first_pass.py", root)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            output = root / "say_do_gap.json"
            copied = root / "intermediate" / "say_do_first_pass.json"
            receipt = root / "intermediate" / "say_do_first_pass.receipt.json"
            self.assertEqual(FIRST.sha256_file(output), FIRST.sha256_file(copied))
            before = receipt.read_bytes()
            again = run_script(FEC / "legislation" / "run_legislation_say_do_first_pass.py", root)
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertEqual(before, receipt.read_bytes())
            manifest = json.loads(receipt.read_text())
            self.assertTrue(manifest["byte_deterministic_repeat"])
            self.assertEqual(manifest["output_contract"]["counts"]["members"], 3)

    def test_first_pass_fails_closed_on_missing_or_stale_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.mkdir(exist_ok=True)
            missing = run_script(FEC / "legislation" / "run_legislation_say_do_first_pass.py", root)
            self.assertEqual(missing.returncode, 2)
            self.assertFalse((root / "say_do_gap.json").exists())
            prepare_first(root)
            passed = run_script(FEC / "legislation" / "run_legislation_say_do_first_pass.py", root)
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            receipt = root / "intermediate" / "say_do_first_pass.receipt.json"
            old_receipt = receipt.read_bytes()
            write_json(root / "category_stance.json", {"changed": True})
            stale = run_script(FEC / "legislation" / "run_legislation_say_do_first_pass.py", root)
            self.assertEqual(stale.returncode, 2)
            self.assertIn("stale for staged inputs", stale.stdout)
            self.assertEqual(old_receipt, receipt.read_bytes())

    def test_second_pass_requires_updated_sequence_and_emits_final_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_first(root)
            passed = run_script(FEC / "legislation" / "run_legislation_say_do_first_pass.py", root)
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            blocked = run_script(FEC / "legislation" / "run_legislation_say_do_second_pass.py", root)
            self.assertEqual(blocked.returncode, 2)
            for name in SECOND.SECOND_SEQUENCE_INPUTS:
                path = root / name
                if not path.exists():
                    write_json(path, {"stage": name})
            write_step_ledger(
                root, through="recompute_dashboard_and_member_desk"
            )
            write_json(root / "member_desk_bundle.json", desk(variant=2))
            second = run_script(FEC / "legislation" / "run_legislation_say_do_second_pass.py", root)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            receipt = json.loads(
                (root / SECOND.SECOND_RECEIPT).read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["pass"], "second")
            self.assertEqual(
                receipt["wrapper"]["sha256"],
                FIRST.sha256_file(FEC / "legislation" / "run_legislation_say_do_second_pass.py"),
            )
            self.assertEqual(
                receipt["declared_output"]["path"],
                str((root / "say_do_gap.json").resolve()),
            )
            before = (root / SECOND.SECOND_RECEIPT).read_bytes()
            again = run_script(FEC / "legislation" / "run_legislation_say_do_second_pass.py", root)
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertEqual(before, (root / SECOND.SECOND_RECEIPT).read_bytes())

    def test_second_pass_fails_closed_when_rank_dependency_turns_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_first(root)
            self.assertEqual(
                run_script(FEC / "legislation" / "run_legislation_say_do_first_pass.py", root).returncode,
                0,
            )
            for name in SECOND.SECOND_SEQUENCE_INPUTS:
                path = root / name
                if not path.exists():
                    write_json(path, {"stage": name})
            write_step_ledger(
                root, through="recompute_dashboard_and_member_desk"
            )
            self.assertEqual(
                run_script(FEC / "legislation" / "run_legislation_say_do_second_pass.py", root).returncode,
                0,
            )
            receipt = root / SECOND.SECOND_RECEIPT
            old = receipt.read_bytes()
            write_json(root / "ranks_python.json", {"changed": True})
            stale = run_script(FEC / "legislation" / "run_legislation_say_do_second_pass.py", root)
            self.assertEqual(stale.returncode, 2)
            self.assertIn("stale for staged inputs", stale.stdout)
            self.assertEqual(old, receipt.read_bytes())

    def test_workspace_root_and_malformed_output_are_rejected(self) -> None:
        with self.assertRaises(FIRST.SayDoError):
            FIRST.validate_run_dir(FIRST.REPO_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_first(root)
            write_json(
                root / "say_do_gap.json",
                {"members": {}, "method": "changed"},
            )
            with self.assertRaises(FIRST.SayDoError):
                FIRST.validate_output(
                    root / "say_do_gap.json",
                    root / "member_desk_bundle.json",
                    root / "data/members/lep_criterion.json",
                )


if __name__ == "__main__":
    unittest.main()
