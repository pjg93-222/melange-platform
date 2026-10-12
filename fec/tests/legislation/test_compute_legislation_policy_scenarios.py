from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

from legislation import compute_legislation_policy_scenarios as scenarios


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def accepted_row(
    key: str,
    category: str,
    bioguide: str,
    name: str,
    role: str,
    *,
    membership: str = "included",
    relationship: str = "current",
    scoring_category: bool = True,
) -> dict:
    congress, measure_type, number = key.split(":")
    return {
        "schema": "accepted-legislation-relationship-v1",
        "measure_key": key,
        "congress": int(congress),
        "measure_type": measure_type,
        "measure_number": int(number),
        "category": category,
        "category_label": {
            "credit_union_capital_requirements": "CU Capital Requirements",
            "credit_union_tax_status": "CU Tax Status",
            "reputation_brand_management": "Reputation & Brand Management",
        }[category],
        "scoring_category": scoring_category,
        "member_bioguide_id": bioguide,
        "member_name": name,
        "role": role,
        "relationship_status": relationship,
        "membership_status": membership,
        "provenance": {"fixture": "official Congress.gov relationship"},
    }


def direction_row(key: str, category: str, sign: int) -> dict:
    congress, measure_type, number = key.split(":")
    return {
        "schema": "legislation-direction-decision-v1",
        "canonical_key": key,
        "congress": int(congress),
        "measure_type": measure_type,
        "number": int(number),
        "category": category,
        "direction": sign,
        "direction_sign": sign,
        "disposition": "included",
        "direction_provenance": {
            "source_url": (
                f"https://www.congress.gov/bill/{congress}th-congress/"
                f"{measure_type}/{number}"
            ),
            "source_reference": "official provisions",
            "rationale": "Official provisions establish the industry effect.",
        },
    }


def fixture_files(root: Path) -> tuple[Path, Path]:
    accepted = root / "accepted.ndjson"
    directions = root / "directions.ndjson"
    write_ndjson(
        accepted,
        [
            accepted_row(
                "119:hr:1",
                "credit_union_capital_requirements",
                "A000001",
                "Alpha, Ann",
                "sponsor",
            ),
            # Duplicate lower role: strongest-role consolidation must retain
            # original_cosponsor only.
            accepted_row(
                "119:hr:1",
                "credit_union_capital_requirements",
                "B000001",
                "Beta, Bob",
                "cosponsor",
            ),
            accepted_row(
                "119:hr:1",
                "credit_union_capital_requirements",
                "B000001",
                "Beta, Bob",
                "original_cosponsor",
            ),
            accepted_row(
                "119:hr:1",
                "credit_union_capital_requirements",
                "D000001",
                "Delta, Dee",
                "cosponsor",
                relationship="withdrawn",
            ),
            accepted_row(
                "119:hr:1",
                "credit_union_tax_status",
                "A000001",
                "Alpha, Ann",
                "sponsor",
                membership="excluded",
            ),
            # Same document number, different type: identity must not collide.
            accepted_row(
                "119:hres:1",
                "credit_union_capital_requirements",
                "A000001",
                "Alpha, Ann",
                "cosponsor",
            ),
            # Exercise the 118=.66 decay.
            accepted_row(
                "118:s:2",
                "credit_union_capital_requirements",
                "B000001",
                "Beta, Bob",
                "sponsor",
            ),
            # The accepted corpus includes a nineteenth monitoring category.
            # It remains visible but needs no direction and contributes zero.
            accepted_row(
                "119:hr:1",
                "reputation_brand_management",
                "A000001",
                "Alpha, Ann",
                "sponsor",
                scoring_category=False,
            ),
        ],
    )
    write_ndjson(
        directions,
        [
            direction_row("119:hr:1", "CU Capital Requirements", 1),
            direction_row("119:hres:1", "CU Capital Requirements", -1),
            direction_row("118:s:2", "CU Capital Requirements", 1),
        ],
    )
    return accepted, directions


def read_ndjson(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class PolicyScenarioTests(unittest.TestCase):
    def test_amendment_scenarios_are_half_weighted(self) -> None:
        rows = [
            {
                **accepted_row(
                    "119:hr:1",
                    "credit_union_capital_requirements",
                    "A000001",
                    "Alpha, Ann",
                    "sponsor",
                ),
                "_input_line": 1,
            },
            {
                **accepted_row(
                    "119:hamdt:2",
                    "credit_union_capital_requirements",
                    "A000001",
                    "Alpha, Ann",
                    "sponsor",
                ),
                "_input_line": 2,
            },
        ]
        directions = {
            ("119:hr:1", "CU Capital Requirements"): {
                "direction_sign": 1,
                "direction_provenance": {"fixture": "bill"},
            },
            ("119:hamdt:2", "CU Capital Requirements"): {
                "direction_sign": 1,
                "direction_provenance": {"fixture": "amendment"},
            },
        }
        events = scenarios.build_events(rows, directions)
        by_measure = {row["measure_key"]: row for row in events}
        self.assertEqual(
            by_measure["119:hr:1"]["policy_contributions"][
                "archive_legacy_3_2_1"
            ],
            3.0,
        )
        self.assertEqual(
            by_measure["119:hamdt:2"]["policy_contributions"][
                "archive_legacy_3_2_1"
            ],
            1.5,
        )
        self.assertEqual(
            by_measure["119:hamdt:2"]["measure_weight"], 0.5
        )
        self.assertEqual(
            by_measure["119:hamdt:2"]["effective_weight"], 0.5
        )

        rows[1]["measure_weight"] = 1.0
        rows[1]["effective_weight"] = 1.0
        with self.assertRaisesRegex(
            scenarios.ScenarioError, "measure_weight"
        ):
            scenarios.build_events(rows, directions)

    def test_all_policies_zero_rules_type_identity_and_idempotence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accepted, directions = fixture_files(root)
            out = root / "scenario"
            first = scenarios.compute(
                accepted_ledger=accepted,
                scoring_directions=directions,
                out=out,
            )
            files = sorted(path for path in out.iterdir())
            first_bytes = {path.name: path.read_bytes() for path in files}

            self.assertTrue(first["scenario_computation_complete"])
            self.assertFalse(first["promotion_ready"])
            self.assertIsNone(first["promotion_policy_selected"])
            events = read_ndjson(out / "role_activity_events.ndjson")
            beta_hr = next(
                row
                for row in events
                if row["measure_key"] == "119:hr:1"
                and row["member_bioguide_id"] == "B000001"
                and row["membership_status"] == "included"
            )
            self.assertEqual(beta_hr["role"], "original_cosponsor")
            self.assertEqual(
                beta_hr["policy_contributions"],
                {
                    "archive_legacy_3_2_1": 2.0,
                    "workbook_legacy_4_2_1": 2.0,
                    "stance_legacy_1_1_1": 1.0,
                },
            )
            zeros = [
                row
                for row in events
                if row["zero_weight_reason"]
                in {"membership_excluded", "relationship_withdrawn"}
            ]
            self.assertEqual(len(zeros), 2)
            self.assertTrue(
                all(
                    set(row["policy_contributions"].values()) == {0.0}
                    for row in zeros
                )
            )
            monitor = next(
                row
                for row in events
                if row["category"]
                == "non_scoring:reputation_brand_management"
            )
            self.assertEqual(
                monitor["zero_weight_reason"], "category_non_scoring"
            )
            self.assertEqual(
                set(monitor["policy_contributions"].values()), {0.0}
            )
            totals = json.loads(
                (out / "scenario_totals.json").read_text(encoding="utf-8")
            )
            alpha = totals["members"]["A000001"]["policy_totals"]
            self.assertEqual(
                alpha,
                {
                    "archive_legacy_3_2_1": 2.0,
                    "workbook_legacy_4_2_1": 3.0,
                    "stance_legacy_1_1_1": 0.0,
                },
            )
            beta = totals["members"]["B000001"]["policy_totals"]
            self.assertEqual(beta["archive_legacy_3_2_1"], 3.98)
            self.assertEqual(beta["workbook_legacy_4_2_1"], 4.64)
            self.assertEqual(beta["stance_legacy_1_1_1"], 1.66)
            pairwise = read_ndjson(out / "policy_pairwise_deltas.ndjson")
            self.assertTrue(
                any(row["score_sign_conflict"] for row in pairwise)
            )

            second = scenarios.compute(
                accepted_ledger=accepted,
                scoring_directions=directions,
                out=out,
            )
            self.assertEqual(first, second)
            self.assertEqual(
                {path.name: path.read_bytes() for path in sorted(out.iterdir())},
                first_bytes,
            )

    def test_explicit_baseline_emits_hash_bound_member_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accepted, directions = fixture_files(root)
            baseline_out = root / "baseline"
            scenarios.compute(
                accepted_ledger=accepted,
                scoring_directions=directions,
                out=baseline_out,
            )
            baseline = baseline_out / "scenario_totals.json"
            current_out = root / "current"
            manifest = scenarios.compute(
                accepted_ledger=accepted,
                scoring_directions=directions,
                baseline_scenario=baseline,
                out=current_out,
            )
            deltas = read_ndjson(
                current_out / "baseline_member_deltas.ndjson"
            )
            self.assertTrue(deltas)
            self.assertTrue(
                all(row["delta_current_minus_baseline"] == 0 for row in deltas)
            )
            self.assertEqual(
                manifest["inputs"]["baseline_scenario"]["sha256"],
                scenarios.sha256_file(baseline),
            )

    def test_missing_direction_fails_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accepted, directions = fixture_files(root)
            rows = read_ndjson(directions)
            write_ndjson(directions, rows[:-1])
            out = root / "blocked"
            with self.assertRaisesRegex(
                scenarios.ScenarioError, "direction coverage"
            ):
                scenarios.compute(
                    accepted_ledger=accepted,
                    scoring_directions=directions,
                    out=out,
                )
            self.assertFalse(out.exists())

    def test_production_output_location_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accepted, directions = fixture_files(root)
            with self.assertRaisesRegex(
                scenarios.ScenarioError, "audits/legfix5"
            ):
                scenarios.compute(
                    accepted_ledger=accepted,
                    scoring_directions=directions,
                    out=ROOT / "scenario-live",
                )


if __name__ == "__main__":
    unittest.main()
