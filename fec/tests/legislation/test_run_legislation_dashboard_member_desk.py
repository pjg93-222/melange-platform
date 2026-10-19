from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, FEC / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DASHBOARD = load_module(
    "run_legislation_dashboard_member_desk_tested",
    "legislation/run_legislation_dashboard_member_desk.py",
)
NETWORK = load_module(
    "run_legislation_network_dependents_tested",
    "legislation/run_legislation_network_dependents.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_network_receipt(
    run_dir: Path, receipt_relative: Path, generation_id: str
) -> Path:
    outputs = {}
    for name in DASHBOARD.NETWORK_OUTPUTS:
        path = write_json(
            run_dir / name,
            {"generation_id": generation_id, "name": name},
        )
        outputs[name] = {
            "path": name,
            "size": path.stat().st_size,
            "sha256": digest(path),
        }
    return write_json(
        run_dir / receipt_relative,
        {
            "schema": DASHBOARD.NETWORK_SCHEMA,
            "status": "PASS",
            "phase": (
                "first"
                if receipt_relative
                == DASHBOARD.NETWORK_FIRST_RECEIPT
                else "second"
            ),
            "generation_id": generation_id,
            "generation_basis_sha256": generation_id,
            "declared_outputs": outputs,
        },
    )


class DashboardMemberDeskPhaseTests(unittest.TestCase):
    def test_dashboard_selects_phase_specific_network_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            first_id = "a" * 64
            second_id = "b" * 64
            write_network_receipt(
                run_dir,
                DASHBOARD.NETWORK_FIRST_RECEIPT,
                first_id,
            )
            first = DASHBOARD.validate_network_receipt(
                run_dir, phase="first"
            )
            self.assertEqual(first["generation_id"], first_id)

            write_network_receipt(
                run_dir,
                DASHBOARD.NETWORK_SECOND_RECEIPT,
                second_id,
            )
            second = DASHBOARD.validate_network_receipt(
                run_dir, phase="second"
            )
            self.assertEqual(second["generation_id"], second_id)

    def test_second_phase_does_not_fall_back_to_first_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            write_network_receipt(
                run_dir,
                DASHBOARD.NETWORK_FIRST_RECEIPT,
                "a" * 64,
            )
            with self.assertRaises(
                (DASHBOARD.DashboardBranchError, OSError)
            ):
                DASHBOARD.validate_network_receipt(
                    run_dir, phase="second"
                )

    def test_network_wrapper_exposes_separate_phase_receipts(self) -> None:
        phase_action = next(
            action
            for action in NETWORK.parser()._actions
            if action.dest == "phase"
        )
        self.assertEqual(
            tuple(phase_action.choices), ("first", "second")
        )
        self.assertNotEqual(
            NETWORK.FIRST_RECEIPT_NAME,
            NETWORK.SECOND_RECEIPT_NAME,
        )
        self.assertEqual(
            NETWORK.RECEIPT_NAME, NETWORK.FIRST_RECEIPT_NAME
        )

    def test_member_desk_producer_writes_the_pinned_freshness(self) -> None:
        # verify_member_desk_freshness only passes if make_member_desk.py
        # uses the DESK_FRESHNESS_PIN file the wrapper hands it.
        import os
        import textwrap
        from unittest import mock

        source = (FEC / "surfaces" / "make_member_desk.py").read_text(
            encoding="utf-8"
        )
        start = source.index("    if os.environ.get('DESK_FRESHNESS_PIN'):")
        end = source.index("    bundle['integrity']['validation']", start)
        inputs = {"ranks_python.json": {"sha256": "a" * 64}}
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            pin = DASHBOARD.write_freshness_pin(run_dir, "generation-1", inputs)
            bundle = {"freshness": {"generated_at": "2026-10-18T09:00:00-05:00"}}
            with mock.patch.dict(os.environ, {"DESK_FRESHNESS_PIN": str(pin)}):
                exec(
                    textwrap.dedent(source[start:end]),
                    {"os": os, "json": json, "bundle": bundle},
                )
            write_json(run_dir / "member_desk_bundle.json", bundle)
            DASHBOARD.verify_member_desk_freshness(run_dir, "generation-1", inputs)


if __name__ == "__main__":
    unittest.main()
