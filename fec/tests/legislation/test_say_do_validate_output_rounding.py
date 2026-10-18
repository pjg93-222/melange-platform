from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

FEC = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "run_legislation_say_do_first_pass_rounding",
    FEC / "legislation" / "run_legislation_say_do_first_pass.py",
)
FIRST = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = FIRST
_spec.loader.exec_module(FIRST)


def row(say_z: float, do_z: float, gap: float, quadrant: str) -> dict:
    return {
        "say_n": 1, "sponsored_cu": 0, "advanced_cu": 0, "adv_rate": None,
        "do_score": 0.0, "say_z": say_z, "do_z": do_z, "gap": gap,
        "quadrant": quadrant,
    }


class ValidateOutputRoundingTests(unittest.TestCase):
    def check(self, member: dict) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "out.json").write_text(json.dumps(
                {"method": FIRST.EXPECTED_METHOD, "members": {"A": member}}))
            (root / "desk.json").write_text(json.dumps(
                {"members": [{"name": "A", "chamber": "house"}]}))
            (root / "lep.json").write_text(json.dumps({"per_member": {}}))
            FIRST.validate_output(root / "out.json", root / "desk.json", root / "lep.json")

    def test_independent_rounding_of_say_do_and_gap_is_accepted(self) -> None:
        # raw say_z=0.0004, do_z=-0.0004 -> say_z 0.0, do_z -0.0, gap 0.001
        self.check(row(0.0, 0.0, 0.001, "aligned"))
        # raw say_z=0.0005, do_z=-0.0005 -> 0.001, -0.001 rounded; gap 0.001
        self.check(row(0.001, -0.001, 0.001, "aligned"))

    def test_real_gap_mismatch_still_fails(self) -> None:
        with self.assertRaises(FIRST.SayDoError):
            self.check(row(0.0, 0.0, 0.01, "aligned"))

    def test_quadrant_boundary_accepts_adjacent_quadrant(self) -> None:
        self.check(row(1.0, 0.0, 1.0, "aligned"))
        self.check(row(1.0, 0.0, 1.0, "talker"))
        self.check(row(0.0, 1.0, -1.0, "aligned"))
        with self.assertRaises(FIRST.SayDoError):
            self.check(row(0.0, 1.0, -1.0, "talker"))
        with self.assertRaises(FIRST.SayDoError):
            self.check(row(1.2, 0.0, 1.2, "aligned"))


if __name__ == "__main__":
    unittest.main()
