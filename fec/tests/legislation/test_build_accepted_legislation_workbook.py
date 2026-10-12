from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from legislation import build_accepted_legislation_workbook as AW  # noqa: E402


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_ndjson(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )
    return path


def receipt(path: Path) -> dict[str, object]:
    return {
        "path": path.name,
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def accepted_row(
    *,
    member: str,
    bioguide: str,
    role: str,
    measure: str = "119:hr:10",
    category: str = "payments",
    membership: str = "included",
    relationship: str = "current",
    tracker: str = "Became Law",
    scoring_category: bool = True,
    historical_membership_only: bool = False,
    historical_relevance_ratified: object = None,
    scoring_eligible: object = None,
) -> dict[str, object]:
    congress, measure_type, number = measure.split(":")
    measure_factor = AW.measure_weight(measure)
    # The producer's full four-term rule.  ``scoring_eligible`` may be forced
    # to model a supplied (rather than produced) ledger.
    eligible = (
        membership == "included"
        and relationship == "current"
        and scoring_category
        and (
            not historical_membership_only
            or historical_relevance_ratified is True
        )
    )
    if scoring_eligible is not None:
        eligible = scoring_eligible
    return {
        "schema": AW.ACCEPTED_SCHEMA,
        "measure_key": measure,
        "congress": int(congress),
        "measure_type": measure_type,
        "measure_number": int(number),
        "display_number": f"H.R.{number}",
        "measure_title": "Payments Test Act",
        "official_title": "Payments Test Act",
        "official_summary": "Updates payment rails.",
        "policy_area": "Finance and Financial Sector",
        "tracker_status": tracker,
        "reported_cosponsor_count": 2,
        "committees": ["House Financial Services"],
        "category": category,
        "category_label": AW.canonical_category(category),
        "scoring_category": scoring_category,
        "member_bioguide_id": bioguide,
        "member_name": member,
        "role": role,
        "relationship_status": relationship,
        "membership_status": membership,
        "historical_membership_only": historical_membership_only,
        "historical_relevance_ratified": historical_relevance_ratified,
        "original_cosponsor": role == "original_cosponsor",
        "decay_weight": AW.EXPECTED_DECAY[int(congress)],
        "measure_weight": measure_factor,
        "effective_weight": round(
            AW.EXPECTED_DECAY[int(congress)] * measure_factor,
            12,
        ),
        "scoring_eligible": eligible,
        "zero_weight_reason": None if eligible else "fixture_zero",
        "provenance": {
            "membership": {
                "official_source_url": (
                    f"https://www.congress.gov/bill/{congress}th-congress/"
                    f"house-bill/{number}"
                )
            }
        },
    }


def numeric_plan(
    generation: str,
    policy: str,
    *,
    resolve_layout: bool,
    manual_evidence_sha256: str = "e" * 64,
) -> dict[str, object]:
    efficiency = {
        "status": "APPROVED",
        "approved_by_user": True,
        "consistent_across_18_categories": resolve_layout,
        "legacy_efficiency_column_count": 17,
        "resolved_category_count": 18 if resolve_layout else 17,
        "layout_resolution": (
            "Explicit 18-category replacement matrix"
            if resolve_layout
            else ""
        ),
    }
    value = {
        "schema": AW.NUMERIC_PLAN_SCHEMA,
        "status": "PASS",
        "generation_id": generation,
        "selected_role_weight_policy": policy,
        "role_weight_policy_reconciled": True,
        "workbook_role_weights_verified": True,
        "measure_weight_policy": AW.measure_weight_policy(),
        "unexplained_cell_count": 0,
        "legislative_efficiency_definition": efficiency,
        "nonbill_numeric_policy": AW.required_nonbill_numeric_policy(
            manual_evidence_sha256
        ),
        "inputs": {
            "accepted_relationship_ledger": {
                "sha256": generation,
            },
            "manual_bill_evidence_csv": {
                "sha256": manual_evidence_sha256,
            },
        },
        "computation_policy_approval": {
            "sha256": "d" * 64,
        },
        "writes": [],
    }
    value["computation_basis_sha256"] = hashlib.sha256(
        AW.canonical_json(
            {
                "generation_id": generation,
                "input_sha256": {
                    key: item["sha256"]
                    for key, item in value["inputs"].items()
                },
                "policy_sha256": value[
                    "computation_policy_approval"
                ]["sha256"],
                "measure_weight_policy": value[
                    "measure_weight_policy"
                ],
                "writes": value["writes"],
            }
        ).encode("utf-8")
    ).hexdigest()
    return value


class AcceptedWorkbookTests(unittest.TestCase):
    def test_amendment_contribution_and_activity_are_half_weighted(self) -> None:
        row = accepted_row(
            member="Amendment Sponsor",
            bioguide="A000001",
            role="sponsor",
            measure="119:hamdt:10",
        )
        result = AW.build_materialization(
            [row],
            {("119:hamdt:10", "Payments"): 1},
            generation_id="f" * 64,
            selected_role_policy="archive_legacy_3_2_1",
        )
        self.assertEqual(
            result["archive_leg"]["Amendment Sponsor"]["Payments"],
            1.5,
        )
        routing = result["routing_audit"]["rows"][0]
        self.assertEqual(routing["congress_decay_weight"], 1.0)
        self.assertEqual(routing["measure_weight"], 0.5)
        self.assertEqual(routing["effective_weight"], 0.5)
        self.assertEqual(routing["activity_decay_weight"], 0.5)
        self.assertEqual(routing["contribution"], 1.5)
        self.assertEqual(result["archive_rows"][1][10], 0.5)

    def test_materialization_preserves_roles_success_and_zero_rows(self) -> None:
        rows = [
            accepted_row(
                member="Sponsor One",
                bioguide="A000001",
                role="sponsor",
            ),
            accepted_row(
                member="Original Two",
                bioguide="B000002",
                role="original_cosponsor",
            ),
            accepted_row(
                member="Later Three",
                bioguide="C000003",
                role="cosponsor",
                tracker="Introduced",
            ),
            accepted_row(
                member="Withdrawn Four",
                bioguide="D000004",
                role="cosponsor",
                relationship="withdrawn",
            ),
            accepted_row(
                member="Excluded Five",
                bioguide="E000005",
                role="cosponsor",
                membership="excluded",
            ),
        ]
        result = AW.build_materialization(
            rows,
            {("119:hr:10", "Payments"): 1},
            generation_id="a" * 64,
            selected_role_policy="workbook_legacy_4_2_1",
        )
        self.assertEqual(
            len(result["model_sheets"]["Sponsor Interactions"]), 2
        )
        self.assertEqual(result["archive_rows"][0], AW.INTERACTION_HEADERS)
        self.assertTrue(
            all(
                len(row) == len(AW.INTERACTION_HEADERS)
                for row in result["archive_rows"]
            )
        )
        self.assertEqual(len(result["archive_rows"]), 4)
        self.assertEqual(
            len(result["model_sheets"]["Original Co-Sponsor Interaction"]),
            2,
        )
        self.assertEqual(
            len(result["model_sheets"]["Co-Sponsor Interactions"]), 2
        )
        self.assertEqual(len(result["model_sheets"]["Success Sponsor"]), 2)
        self.assertEqual(
            len(result["model_sheets"]["Success Original Co-Sponsor"]), 2
        )
        self.assertEqual(result["archive_leg"]["Sponsor One"]["Payments"], 3.0)
        self.assertEqual(result["archive_leg"]["Original Two"]["Payments"], 2.0)
        self.assertEqual(result["archive_leg"]["Later Three"]["Payments"], 1.0)
        routing = result["routing_audit"]
        self.assertEqual(
            routing["summary"]["dispositions"]["withdrawn_relationship"], 1
        )
        self.assertEqual(
            routing["summary"]["dispositions"]["excluded_membership"], 1
        )
        zero_rows = [
            row
            for row in routing["rows"]
            if row["disposition"]
            in {"withdrawn_relationship", "excluded_membership"}
        ]
        self.assertTrue(all(row["contribution"] == 0 for row in zero_rows))
        self.assertTrue(all(row["decay_weight"] == 0 for row in zero_rows))

    def test_neutral_direction_is_activity_with_zero_scoring(self) -> None:
        rows = [
            accepted_row(
                member="Sponsor One",
                bioguide="A000001",
                role="sponsor",
            )
        ]
        result = AW.build_materialization(
            rows,
            {("119:hr:10", "Payments"): 0},
            generation_id="b" * 64,
            selected_role_policy="archive_legacy_3_2_1",
        )
        self.assertEqual(result["archive_leg"], {})
        self.assertEqual(len(result["archive_rows"]), 2)
        self.assertEqual(len(result["archive_rows"][1]), 13)
        self.assertEqual(
            len(result["model_sheets"]["Sponsor Interactions"]), 2
        )
        self.assertEqual(len(result["model_sheets"]["Success Sponsor"]), 2)
        row = result["routing_audit"]["rows"][0]
        self.assertEqual(row["disposition"], "neutral_direction")
        self.assertEqual(row["contribution"], 0.0)
        self.assertEqual(row["scoring_contribution"], 0.0)
        self.assertEqual(row["archive_role_weight"], 0.0)
        self.assertEqual(row["decay_weight"], 0.0)
        self.assertEqual(row["scoring_decay_weight"], 0.0)
        self.assertEqual(
            row["activity_decay_weight"], AW.EXPECTED_DECAY[119]
        )
        self.assertTrue(row["activity_eligible"])
        self.assertTrue(row["included_in_activity_workbooks"])
        self.assertFalse(row["scoring_eligible"])
        policy = result["routing_audit"]["activity_policy"]
        self.assertTrue(policy["neutral_direction_retained_as_activity"])
        self.assertEqual(policy["canonical_all_bills_column_count"], 13)

    def test_unrecognized_tracker_status_blocks_success_inference(self) -> None:
        rows = [
            accepted_row(
                member="Sponsor One",
                bioguide="A000001",
                role="sponsor",
                tracker="Some unofficial summary",
            )
        ]
        with self.assertRaisesRegex(
            AW.WorkbookBuildError, "tracker_status"
        ):
            AW.build_materialization(
                rows,
                {("119:hr:10", "Payments"): 1},
                generation_id="c" * 64,
                selected_role_policy="workbook_legacy_4_2_1",
            )

    def test_legacy_17_column_efficiency_claim_needs_explicit_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generation = "d" * 64
            policy = "workbook_legacy_4_2_1"
            plan_path = write_json(
                root / "plan.json",
                numeric_plan(generation, policy, resolve_layout=False),
            )
            manifest = {"numeric_recompute_plan": receipt(plan_path)}
            with self.assertRaisesRegex(
                AW.WorkbookBuildError, "only 17 efficiency columns"
            ):
                AW.validate_numeric_recompute_plan(
                    root,
                    manifest,
                    generation_id=generation,
                    selected_policy=policy,
                    manual_evidence_sha256="e" * 64,
                )
            write_json(
                plan_path,
                numeric_plan(generation, policy, resolve_layout=True),
            )
            manifest = {"numeric_recompute_plan": receipt(plan_path)}
            validated = AW.validate_numeric_recompute_plan(
                root,
                manifest,
                generation_id=generation,
                selected_policy=policy,
                manual_evidence_sha256="e" * 64,
            )
            self.assertEqual(validated["path"], plan_path.resolve())

    def _validate_rows(self, rows: list[dict[str, object]]) -> dict[str, object]:
        """Drive the real accepted-generation validator over supplied rows."""

        with tempfile.TemporaryDirectory() as directory:
            return self._build_and_validate(Path(directory), rows)

    def _build_and_validate(
        self, root: Path, rows: list[dict[str, object]]
    ) -> dict[str, object]:
        ledger = write_ndjson(root / "000_accepted.ndjson", rows)
        generation = hashlib.sha256(ledger.read_bytes()).hexdigest()
        policy = "workbook_legacy_4_2_1"
        approval = write_json(
            root / "001_approval.json",
            {
                "schema": AW.ROLE_APPROVAL_SCHEMA,
                "status": "APPROVED",
                "human_approved": True,
                "approved_by_user": True,
                "selected_role_weight_policy": policy,
                "selected_method_profile": AW.ROLE_METHOD_PROFILE_ID,
                "selected_policy": AW.ROLE_METHOD_PROFILE_ID,
                "consumer_methods": AW.ROLE_METHOD_PROFILE,
                "applies_to": list(AW.ROLE_METHOD_PROFILE),
                "accepted_ledger_sha256": generation,
            },
        )
        plan = write_json(
            root / "002_plan.json",
            numeric_plan(
                generation,
                policy,
                resolve_layout=True,
                manual_evidence_sha256="f" * 64,
            ),
        )
        manual = root / "003_manual_bill_evidence.csv"
        manual.write_text(
            "Category,Bill,Role,Member,Congress\n"
            "CFPB,LTR-TEST,sponsor,Sponsor One,119\n",
            encoding="utf-8",
        )
        manual_sha = hashlib.sha256(manual.read_bytes()).hexdigest()
        write_json(
            plan,
            numeric_plan(
                generation,
                policy,
                resolve_layout=True,
                manual_evidence_sha256=manual_sha,
            ),
        )
        manifest = {
            "schema": AW.ACCEPTED_MANIFEST_SCHEMA,
            "status": "complete",
            "generation_complete": True,
            "upstream_acceptance_passed": True,
            "staged_only": True,
            "production_artifacts_written": False,
            "promotion_ready": True,
            "role_weight_policy_reconciled": True,
            "selected_role_weight_policy": policy,
            "selected_method_profile": AW.ROLE_METHOD_PROFILE_ID,
            "consumer_role_methods": AW.ROLE_METHOD_PROFILE,
            "role_weight_policy_scenarios": {
                AW.LEGACY_ARCHIVE_POLICY: AW.ARCHIVE_ROLE_WEIGHT,
                policy: {
                    "sponsor": 4.0,
                    "original_cosponsor": 2.0,
                    "cosponsor": 1.0,
                },
            },
            "decay_policy": {
                "weights": {
                    str(key): value
                    for key, value in AW.EXPECTED_DECAY.items()
                }
            },
            "measure_weight_policy": AW.measure_weight_policy(),
            "canonical_ledger_sha256": generation,
            "canonical_ledger_row_count": len(rows),
            "role_weight_policy_approval": receipt(approval),
            "numeric_recompute_plan": receipt(plan),
            "manual_bill_evidence": receipt(manual),
        }
        write_json(root / "manifest.json", manifest)
        return AW.validate_accepted_generation(root)

    def test_accepted_generation_requires_human_policy_and_numeric_plan(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                accepted_row(
                    member="Sponsor One",
                    bioguide="A000001",
                    role="sponsor",
                )
            ]
            result = self._build_and_validate(root, rows)
            self.assertEqual(
                result["generation_id"],
                hashlib.sha256(
                    (root / "000_accepted.ndjson").read_bytes()
                ).hexdigest(),
            )
            self.assertTrue(
                result["role_weight_policy_approval"]["value"][
                    "human_approved"
                ]
            )

    def test_numeric_plan_requires_bound_measure_weight_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generation = "a" * 64
            policy = "workbook_legacy_4_2_1"
            value = numeric_plan(
                generation,
                policy,
                resolve_layout=True,
            )
            value.pop("measure_weight_policy")
            plan = write_json(root / "plan.json", value)
            manifest = {"numeric_recompute_plan": receipt(plan)}
            with self.assertRaisesRegex(
                AW.WorkbookBuildError, "numeric recompute plan"
            ):
                AW.validate_numeric_recompute_plan(
                    root,
                    manifest,
                    generation_id=generation,
                    selected_policy=policy,
                    manual_evidence_sha256="e" * 64,
                )

    def test_shadow_context_refuses_nonshadow_or_mismatched_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            test_root = Path(directory).resolve()
            run = test_root / "run_a"
            (run / "inputs").mkdir(parents=True)
            environment = {
                "LEGISLATION_SHADOW_MODE": "1",
                "LEGISLATION_SHADOW_RUN_DIR": str(run),
                "LEGISLATION_SHADOW_INPUT_ROOT": str(run / "inputs"),
                "LEGISLATION_SHADOW_TEST_ROOT": str(test_root),
                "MONEYBALL_ROOT": str(run),
            }
            with patch.dict(os.environ, environment, clear=False):
                self.assertEqual(
                    AW.validate_shadow_context(None, None),
                    (run.resolve(), (run / "inputs").resolve()),
                )
                with self.assertRaisesRegex(
                    AW.WorkbookBuildError, "exactly equal"
                ):
                    AW.validate_shadow_context(run / "other", None)
            with patch.dict(
                os.environ,
                {"LEGISLATION_SHADOW_MODE": "0"},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    AW.WorkbookBuildError, "SHADOW_MODE"
                ):
                    AW.validate_shadow_context(run, run / "inputs")

    # -- DEFECT 4: verify the same four terms the producer computes ---------

    def test_unratified_historical_row_cannot_claim_scoring_eligibility(
        self,
    ) -> None:
        """A SUPPLIED ledger used to fail OPEN here.

        The three-term recomputation never read the ratification fields, so a
        row asserting historical-membership-only, ratification false, and
        scoring eligible true satisfied the verifier outright.
        """

        forged = accepted_row(
            member="Member 001",
            bioguide="A000001",
            role="sponsor",
            historical_membership_only=True,
            historical_relevance_ratified=False,
            scoring_eligible=True,
        )
        authorized, error = AW.historical_scoring_authorization(forged)
        self.assertIsNone(error)
        self.assertFalse(authorized)
        with self.assertRaisesRegex(
            AW.WorkbookBuildError, "scoring eligibility is inconsistent"
        ):
            self._validate_rows([forged])

    def test_ratified_historical_row_may_score(self) -> None:
        row = accepted_row(
            member="Member 001",
            bioguide="A000001",
            role="sponsor",
            historical_membership_only=True,
            historical_relevance_ratified=True,
        )
        self.assertTrue(row["scoring_eligible"])
        self._validate_rows([row])

    def test_absent_historical_provenance_fails_closed(self) -> None:
        row = accepted_row(
            member="Member 001", bioguide="A000001", role="sponsor"
        )
        row.pop("historical_membership_only")
        with self.assertRaisesRegex(
            AW.WorkbookBuildError, "historical_membership_only must be a boolean"
        ):
            self._validate_rows([row])

    def test_non_historical_row_may_not_carry_a_ratification_verdict(
        self,
    ) -> None:
        row = accepted_row(
            member="Member 001", bioguide="A000001", role="sponsor"
        )
        row["historical_relevance_ratified"] = True
        with self.assertRaisesRegex(
            AW.WorkbookBuildError,
            "must leave historical_relevance_ratified null",
        ):
            self._validate_rows([row])

    def test_both_verifiers_agree_with_the_producer_four_term_rule(
        self,
    ) -> None:
        """Exhaustive truth table across the assembler and workbook builder."""

        from legislation import assemble_approved_legislation_generation as AG

        for historical_only in (True, False):
            for ratified in (True, False, None):
                row = {
                    "historical_membership_only": historical_only,
                    "historical_relevance_ratified": ratified,
                }
                workbook = AW.historical_scoring_authorization(row)
                assembler = AG.historical_scoring_authorization(row)
                self.assertEqual(workbook, assembler, row)
                if historical_only and ratified is None:
                    self.assertIsNotNone(workbook[1], row)
                elif not historical_only and ratified is not None:
                    self.assertIsNotNone(workbook[1], row)
                else:
                    self.assertIsNone(workbook[1], row)
                    self.assertEqual(
                        workbook[0],
                        (not historical_only) or ratified is True,
                        row,
                    )

    def test_xlsx_authoring_contract_is_artifact_tool_only(self) -> None:
        python = (FEC / "legislation" / "build_accepted_legislation_workbook.py").read_text(
            encoding="utf-8"
        )
        helper = (
            FEC / "build_accepted_legislation_workbook.cjs"
        ).read_text(encoding="utf-8")
        for forbidden in ("openpyxl", "xlsxwriter", "pandas"):
            self.assertNotIn(forbidden, python)
        self.assertIn("@oai/artifact-tool", helper)
        self.assertIn("SpreadsheetFile.importXlsx", helper)
        self.assertIn("SpreadsheetFile.exportXlsx", helper)
        self.assertIn("ARTIFACT_TOOL_NODE_MODULES", helper)
        self.assertIn(AW.SCHEMA, python)
        self.assertIn(
            "intermediate / \"accepted_legislation_workbook.receipt.json\"",
            python,
        )


if __name__ == "__main__":
    unittest.main()
