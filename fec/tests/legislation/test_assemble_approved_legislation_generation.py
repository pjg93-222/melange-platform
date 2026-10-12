from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


FEC = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FEC))
sys.path.insert(0, str(TESTS))

from legislation import assemble_approved_legislation_generation as AG  # noqa: E402
from legislation import build_accepted_legislation_workbook as AW  # noqa: E402
from legislation import build_legislation_workbook_numeric_plan as NP  # noqa: E402
import test_build_legislation_workbook_numeric_plan as NUMERIC_FIXTURE  # noqa: E402
from test_build_legislation_workbook_numeric_plan import (  # noqa: E402
    Fixture as NumericFixture,
    write_json,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_receipt(path: Path) -> dict[str, object]:
    return {
        "path": path.name,
        "size": path.stat().st_size,
        "sha256": sha256(path),
    }


_BASE_ACCEPTED_ROW = NUMERIC_FIXTURE.accepted_row


def accepted_row_with_historical_provenance(
    *args: object, **kwargs: object
) -> dict[str, object]:
    """Give the shared numeric-plan rows the producer's historical fields.

    ``build_accepted_legislation_generation`` always emits
    ``historical_membership_only`` / ``historical_relevance_ratified``; the
    shared numeric-plan fixture predates them.  The assembler now recomputes
    the full four-term scoring rule, so a row without them can no longer be
    verified.  Patched at fixture-construction time so every downstream hash
    binding is computed over the same bytes.
    """

    row = dict(_BASE_ACCEPTED_ROW(*args, **kwargs))
    row.setdefault("historical_membership_only", False)
    row.setdefault("historical_relevance_ratified", None)
    return row


def unratified_historical_row(
    *args: object, **kwargs: object
) -> dict[str, object]:
    """A supplied row that claims scoring on unratified historical evidence."""

    row = dict(_BASE_ACCEPTED_ROW(*args, **kwargs))
    row["historical_membership_only"] = True
    row["historical_relevance_ratified"] = False
    row["scoring_eligible"] = True
    return row


class AssemblyFixture:
    def __init__(self, root: Path, row_factory: object = None):
        self.root = root
        with patch.object(
            NUMERIC_FIXTURE,
            "accepted_row",
            row_factory or accepted_row_with_historical_provenance,
        ):
            self.numeric = NumericFixture(root)
        self.generation_id = sha256(self.numeric.accepted)
        self.plan = root / "numeric_plan.json"
        write_json(self.plan, self.numeric.build())
        self.role = root / "role_approval.json"
        write_json(
            self.role,
            {
                "schema": AG.ROLE_APPROVAL_SCHEMA,
                "status": "APPROVED",
                "human_approved": True,
                "approved_by_user": True,
                "selected_role_weight_policy":
                    "workbook_legacy_4_2_1",
                "selected_method_profile": AG.ROLE_METHOD_PROFILE_ID,
                "selected_policy": AG.ROLE_METHOD_PROFILE_ID,
                "consumer_methods": AG.ROLE_METHOD_PROFILE,
                "applies_to": list(AG.ROLE_METHOD_PROFILE),
                "accepted_ledger_sha256": self.generation_id,
            },
        )
        self.accepted_manifest = root / "accepted_manifest.json"
        self.coverage_hashes = {
            "strict_discovery_manifest_sha256": "1" * 64,
            "final_merged_manifest_sha256": "2" * 64,
            "membership_review_manifest_sha256": "3" * 64,
            "relationships_manifest_sha256": "4" * 64,
            "reconciliation_report_sha256": "5" * 64,
        }
        write_json(
            self.accepted_manifest,
            {
                "schema": AG.MANIFEST_SCHEMA,
                "status": "complete",
                "generation_complete": True,
                "upstream_acceptance_passed": True,
                "staged_only": True,
                "production_artifacts_written": False,
                "canonical_ledger_schema": AG.LEDGER_SCHEMA,
                "canonical_ledger_file": self.numeric.accepted.name,
                "canonical_ledger_sha256": self.generation_id,
                "canonical_ledger_row_count": 4,
                "input_files": {
                    "strict": "upstream/strict/manifest.json",
                    "final": "upstream/final/manifest.json",
                },
                "input_hashes": self.coverage_hashes,
                "membership_unresolved_count": 0,
                "relationship_unresolved_measure_count": 0,
                "decay_policy": {
                    "name":
                        "aggressive_congress_decay_1_0.66_0.33_0.16",
                    "weights": {
                        str(key): value
                        for key, value in AG.EXPECTED_DECAY.items()
                    },
                },
                "measure_weight_policy": AG.measure_weight_policy(),
                "role_weight_policy_scenarios": NP.ROLE_WEIGHTS,
                "selected_role_weight_policy": None,
                "role_weight_policy_reconciled": False,
                "promotion_ready": False,
                "promotion_blockers": ["role_weight_policy_unresolved"],
            },
        )
        self.direction_manifest = root / "direction_manifest.json"
        write_json(
            self.direction_manifest,
            {
                "schema": AG.DIRECTION_MANIFEST_SCHEMA,
                "status": "PASS",
                "acceptance_passed": True,
                "production_write_prohibited": True,
                "inputs": {
                    "accepted_ledger_sha256": self.generation_id,
                },
                "outputs": {
                    "scoring_directions.ndjson":
                        file_receipt(self.numeric.directions)
                },
            },
        )

    def args(self, out: Path) -> dict[str, Path]:
        return {
            "accepted_manifest_path": self.accepted_manifest,
            "accepted_ledger_path": self.numeric.accepted,
            "direction_manifest_path": self.direction_manifest,
            "direction_ledger_path": self.numeric.directions,
            "manual_evidence_path": self.numeric.manual,
            "role_approval_path": self.role,
            "computation_approval_path": self.numeric.policy,
            "audited_layout_path": self.numeric.layout,
            "numeric_plan_path": self.plan,
            "out_dir": out,
        }


class ApprovedGenerationAssemblyTests(unittest.TestCase):
    def test_assembles_manifest_adjacent_hash_bound_generation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            out = Path(raw) / "approved"
            source_bytes = fixture.numeric.accepted.read_bytes()
            manifest = AG.assemble(**fixture.args(out))

            self.assertTrue(manifest["promotion_ready"])
            self.assertTrue(manifest["role_weight_policy_reconciled"])
            self.assertEqual(
                manifest["selected_role_weight_policy"],
                "workbook_legacy_4_2_1",
            )
            self.assertEqual(
                manifest["input_hashes"], fixture.coverage_hashes
            )
            self.assertEqual(
                (out / AG.OUTPUT_NAMES["canonical_ledger"]).read_bytes(),
                source_bytes,
            )
            required = {
                "canonical_ledger",
                "role_weight_policy_approval",
                "computation_policy_approval",
                "manual_bill_evidence",
                "audited_layout",
                "numeric_recompute_plan",
            }
            self.assertTrue(required.issubset(manifest["outputs"]))
            for item in manifest["outputs"].values():
                path = out / item["path"]
                self.assertTrue(path.is_file())
                self.assertEqual(sha256(path), item["sha256"])
                self.assertEqual(path.stat().st_size, item["size"])
            self.assertFalse(manifest["production_artifacts_written"])
            self.assertFalse(
                manifest["approval_assembly"]["production_written"]
            )
            self.assertEqual(
                manifest["press_member_alignment"][
                    "selected_treatment"
                ],
                NP.PRESS_ALIGNMENT_PRESERVE_LITERAL,
            )

            accepted = AW.validate_accepted_generation(out)
            self.assertEqual(
                accepted["generation_id"], fixture.generation_id
            )

    def test_stale_role_approval_blocks_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            approval = json.loads(
                fixture.role.read_text(encoding="utf-8")
            )
            approval["accepted_ledger_sha256"] = "9" * 64
            write_json(fixture.role, approval)
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(AG.AssemblyError, "role-weight"):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_manual_evidence_change_blocks_numeric_plan_binding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            with fixture.numeric.manual.open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write("\n")
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(
                AG.AssemblyError, "manual_bill_evidence_csv"
            ):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_plan_requires_bound_measure_weight_policy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            plan = json.loads(
                fixture.plan.read_text(encoding="utf-8")
            )
            plan.pop("measure_weight_policy")
            write_json(fixture.plan, plan)
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(
                AG.AssemblyError, "complete PASS"
            ):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_unapproved_computation_policy_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            policy = json.loads(
                fixture.numeric.policy.read_text(encoding="utf-8")
            )
            policy["status"] = "PENDING"
            policy["approved_by_user"] = False
            write_json(fixture.numeric.policy, policy)
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(
                AG.AssemblyError, "computation approval"
            ):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_plan_cannot_invent_a_cfpb_bill_route(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            plan = json.loads(fixture.plan.read_text(encoding="utf-8"))
            route = plan["cfpb_accepted_bill_routing"][
                "sponsor_original_route"
            ]
            route.update(
                {
                    "status": "SELECTED",
                    "approved_by_user": True,
                    "selected_route": "invented_surface_v1",
                    "implementation_id": "numeric-plan:invented-v1",
                }
            )
            write_json(fixture.plan, plan)
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(
                AG.AssemblyError, "CFPB accepted-bill routing"
            ):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_plan_cannot_invent_press_member_alignment_policy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            plan = json.loads(
                fixture.plan.read_text(encoding="utf-8")
            )
            plan["press_member_alignment"][
                "selected_treatment"
            ] = NP.PRESS_ALIGNMENT_REPAIR_BY_BIOGUIDE
            write_json(fixture.plan, plan)
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(
                AG.AssemblyError, "press-member alignment"
            ):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_assembly_rejects_unapproved_press_alignment_policy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            policy = json.loads(
                fixture.numeric.policy.read_text(encoding="utf-8")
            )
            alignment = policy["layout_policy"][
                "press_member_alignment"
            ]
            alignment["status"] = "PENDING"
            alignment["approved_by_user"] = False
            write_json(fixture.numeric.policy, policy)
            plan = json.loads(
                fixture.plan.read_text(encoding="utf-8")
            )
            policy_hash = sha256(fixture.numeric.policy)
            plan["computation_policy_approval"]["sha256"] = policy_hash
            input_hashes = {
                key: value["sha256"]
                for key, value in plan["inputs"].items()
            }
            plan["computation_basis_sha256"] = hashlib.sha256(
                AG.canonical_bytes(
                    {
                        "generation_id": fixture.generation_id,
                        "input_sha256": input_hashes,
                        "policy_sha256": policy_hash,
                        "writes": plan["writes"],
                    }
                )
            ).hexdigest()
            write_json(fixture.plan, plan)
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(
                AG.AssemblyError,
                "press-member alignment policy",
            ):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_unapproved_or_missing_17_18_layout_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            plan = json.loads(fixture.plan.read_text(encoding="utf-8"))
            plan["efficiency_layout"]["category_mappings"].pop("CU Tax Status")
            write_json(fixture.plan, plan)
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(
                AG.AssemblyError, "efficiency mappings"
            ):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_missing_cu_tax_efficiency_treatment_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            plan = json.loads(fixture.plan.read_text(encoding="utf-8"))
            plan["efficiency_layout"].pop("cu_tax_efficiency_treatment")
            write_json(fixture.plan, plan)
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(
                AG.AssemblyError, "CU Tax efficiency treatment"
            ):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_direction_manifest_must_bind_accepted_generation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            direction = json.loads(
                fixture.direction_manifest.read_text(encoding="utf-8")
            )
            direction["inputs"]["accepted_ledger_sha256"] = "8" * 64
            write_json(fixture.direction_manifest, direction)
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(AG.AssemblyError, "not bound"):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_existing_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw))
            out = Path(raw) / "approved"
            out.mkdir()
            marker = out / "owner.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(AG.AssemblyError, "already exist"):
                AG.assemble(**fixture.args(out))
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    # -- DEFECT 4: verify all four terms the producer computes -------------

    def test_unratified_historical_row_cannot_claim_scoring_eligibility(
        self,
    ) -> None:
        """A SUPPLIED ledger used to fail OPEN here.

        The three-term recomputation never read the ratification fields, so a
        row asserting historical-membership-only, ratification false, and
        scoring eligible true satisfied the assembler outright.
        """

        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(
                Path(raw), row_factory=unratified_historical_row
            )
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(
                AG.AssemblyError, "scoring eligibility inconsistent"
            ):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())

    def test_absent_historical_provenance_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = AssemblyFixture(Path(raw), row_factory=_BASE_ACCEPTED_ROW)
            out = Path(raw) / "approved"
            with self.assertRaisesRegex(
                AG.AssemblyError,
                "historical_membership_only must be a boolean",
            ):
                AG.assemble(**fixture.args(out))
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
