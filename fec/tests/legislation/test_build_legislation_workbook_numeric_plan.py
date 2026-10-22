from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

import roster  # noqa: E402
from legislation import build_accepted_legislation_workbook as AW  # noqa: E402
from legislation import build_legislation_workbook_numeric_plan as NP  # noqa: E402
from legislation import recompute_legislative_workbook as RW  # noqa: E402

RAW_LAST = roster.last_row("Legislative Score - Raw")


def write_json(path: Path, value: object) -> Path:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_ndjson(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def accepted_row(
    measure: str,
    category: str,
    bioguide: str,
    name: str,
    *,
    congress: int,
    role: str,
    tracker_status: str,
) -> dict[str, object]:
    measure_factor = NP.measure_weight(measure)
    return {
        "schema": NP.ACCEPTED_SCHEMA,
        "measure_key": measure,
        "congress": congress,
        "decay_weight": NP.EXPECTED_DECAY[congress],
        "measure_weight": measure_factor,
        "effective_weight": round(
            NP.EXPECTED_DECAY[congress] * measure_factor,
            12,
        ),
        "category_label": category,
        "member_bioguide_id": bioguide,
        "member_name": name,
        "role": role,
        "tracker_status": tracker_status,
        "membership_status": "included",
        "relationship_status": "current",
        "scoring_category": True,
        "scoring_eligible": True,
    }


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.accepted = root / "accepted.ndjson"
        self.directions = root / "directions.ndjson"
        self.manual = root / "manual_bill_evidence.csv"
        self.layout = root / "layout.json"
        self.policy = root / "policy.json"
        self.baseline = root / "baseline.json"
        self.workbook_sha = "f" * 64

        members = [
            {
                "name": f"Member {index:03d}",
                "bioguide_id": f"A{index + 1:06d}",
                "raw_row": index + 3,
                "sublayer_row": index + 4,
            }
            for index in range(roster.count())
        ]
        accepted = [
            accepted_row(
                "119:hr:1",
                "Payments",
                "A000001",
                "Member 000",
                congress=119,
                role="sponsor",
                tracker_status="Passed House",
            ),
            accepted_row(
                "118:s:2",
                "Payments",
                "A000002",
                "Member 001",
                congress=118,
                role="original_cosponsor",
                tracker_status="Introduced",
            ),
            accepted_row(
                "118:hr:3",
                "Technology & Emerging",
                "A000003",
                "Member 002",
                congress=118,
                role="sponsor",
                tracker_status="Became Law",
            ),
            accepted_row(
                "117:s:4",
                "Member Business Lending",
                "A000004",
                "Member 003",
                congress=117,
                role="sponsor",
                tracker_status="Passed Senate",
            ),
        ]
        write_ndjson(self.accepted, accepted)
        write_ndjson(
            self.directions,
            [
                {
                    "schema": NP.DIRECTION_SCHEMA,
                    "canonical_key": row["measure_key"],
                    "category": row["category_label"],
                    "direction_sign": 1,
                }
                for row in accepted
            ],
        )
        with self.manual.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "Bill",
                    "Member",
                    "Congress",
                    "Role",
                    "Direction",
                    "DecayWeight",
                    "Category",
                ],
            )
            writer.writeheader()
            writer.writerows(
                [
                    {
                        "Bill": "LTR-117-CFPB-1",
                        "Member": "Member 000",
                        "Congress": "117",
                        "Role": "Sponsor",
                        "Direction": "1",
                        "DecayWeight": "0.64",
                        "Category": "CFPB",
                    },
                    {
                        "Bill": "LTR-118-CFPB-2",
                        "Member": "Member 001",
                        "Congress": "118",
                        "Role": "Original Cosponsor",
                        "Direction": "1",
                        "DecayWeight": "0.8",
                        "Category": "CFPB",
                    },
                ]
            )
        write_json(
            self.baseline,
            {
                "schema": NP.BASELINE_SCHEMA,
                "files": [
                    {
                        "copy": "Moneyball Main Version - Blended.xlsx",
                        "size": 1,
                        "sha256": self.workbook_sha,
                    }
                ],
            },
        )
        slots = [
            {
                "column": column,
                "categories": [category],
                "aggregation": "identity",
            }
            for column, category in zip(
                NP.EFFICIENCY_COLUMNS, NP.CATEGORIES[:-1]
            )
        ]
        write_json(
            self.layout,
            {
                "schema": NP.LAYOUT_SCHEMA,
                "status": "PASS",
                "engine": "@oai/artifact-tool",
                "xlsx_opened_by_python": False,
                "production_written": False,
                "audit_complete": True,
                "source_workbook_sha256": self.workbook_sha,
                "raw_block_ranges": NP.BLOCK_RANGES,
                "sublayer_category_columns": NP.CATEGORY_SUBLAYER_COLUMNS,
                "members": members,
                "sublayer_nonlegislative_values": {
                    category: [0.0] * roster.count() for category in NP.CATEGORIES
                },
                "sublayer_legislative_weights": {
                    category: [
                        4.0 / 14.0,
                        0.0,
                        2.0 / 14.0,
                        0.0,
                        1.0 / 14.0,
                    ]
                    for category in NP.CATEGORIES
                },
                "press_literal_row_alignment": {
                    "status":
                        NP.PRESS_ALIGNMENT_UNRESOLVED_STATUS,
                    "member_join_applied": False,
                    "mismatch_count": 5,
                    "mismatches": [
                        {
                            "member_index": index,
                            "legislative_member":
                                f"Member {index:03d}",
                            "press_literal_member":
                                f"Member {index + 1:03d}",
                        }
                        for index in range(5)
                    ],
                    "reconstruction_semantics":
                        NP.PRESS_ALIGNMENT_PRESERVE_LITERAL,
                },
                "efficiency_slots": slots,
            },
        )
        self.write_policy()

    @property
    def paths(self) -> dict[str, Path]:
        return {
            "accepted_relationship_ledger": self.accepted,
            "scoring_direction_ledger": self.directions,
            "manual_bill_evidence_csv": self.manual,
            "audited_member_layout": self.layout,
            "frozen_baseline_manifest": self.baseline,
        }

    def write_policy(self, **overrides: object) -> None:
        slots = json.loads(self.layout.read_text(encoding="utf-8"))[
            "efficiency_slots"
        ]
        alignment = json.loads(
            self.layout.read_text(encoding="utf-8")
        )["press_literal_row_alignment"]
        value: dict[str, object] = {
            "schema": NP.POLICY_SCHEMA,
            "status": "APPROVED",
            "approved_by_user": True,
            "generation_id": sha256(self.accepted),
            "input_sha256": {
                label: sha256(path) for label, path in self.paths.items()
            },
            "selected_role_weight_policy": "workbook_legacy_4_2_1",
            "role_weight_policy_reconciled": True,
            "role_policy": {
                "weights": NP.ROLE_WEIGHTS["workbook_legacy_4_2_1"],
                "raw_activity_encoding": "signed_sum_congress_decay",
                "average_encoding":
                    "signed_mean_congress_decay_per_relationship",
                "workbook_row2_applies_role_weights": True,
            },
            "legislative_efficiency_definition": {
                "status": "APPROVED",
                "approved_by_user": True,
                "consistent_across_18_categories": True,
                "definition_id":
                    "uniform-decay-weighted-advanced-rate-v1",
                "numerator":
                    "decay-weighted accepted advanced relationships",
                "denominator":
                    "decay-weighted accepted scoring opportunities",
                "empty_role_handling": "explicit zero",
                "success_action_rule":
                    "tracker_status in approved advanced set",
                "weighting_basis": "canonical Congress decay",
                "opportunity_roles": [
                    "sponsor",
                    "original_cosponsor",
                ],
                "generic_rate": "pooled_sponsor_and_original",
                "scale": "percentage_points",
                "precision_decimals": 1,
                "ltr_success_handling":
                    "activity_only_excluded_from_efficiency",
                "legacy_efficiency_column_count": 17,
                "resolved_category_count": 18,
                "success_tracker_statuses": sorted(NP.ADVANCED_STATUSES),
                "layout_resolution":
                    "AG explicitly averages Examination and CU Capital",
            },
            "layout_policy": {
                "status": "APPROVED",
                "approved_by_user": True,
                "layout_sha256": sha256(self.layout),
                "category_columns": NP.CATEGORY_SUBLAYER_COLUMNS,
                "efficiency_slots": slots,
                "cu_tax_efficiency_treatment": {
                    "schema": NP.CU_TAX_TREATMENT_SCHEMA,
                    "status": "APPROVED",
                    "approved_by_user": True,
                    "selected_treatment": NP.CU_TAX_PRESERVE_OMISSION,
                    "omitted_category": NP.CU_TAX_CATEGORY,
                    "legacy_physical_column_count": 17,
                    "resolved_category_count": 18,
                    "source_metric": "generic_success_rate",
                    "raw_source_range":
                        f"Legislative Score - Raw!FB3:FB{RAW_LAST}",
                    "normalized_source_range":
                        f"Legislative Score - Normalized!FB3:FB{RAW_LAST}",
                    "merge_target_category": None,
                    "merge_target_column": None,
                    "rank_effect_acknowledged": True,
                },
                "press_member_alignment": {
                    "schema": NP.PRESS_ALIGNMENT_POLICY_SCHEMA,
                    "status": "APPROVED",
                    "approved_by_user": True,
                    "selected_treatment":
                        NP.PRESS_ALIGNMENT_PRESERVE_LITERAL,
                    "layout_sha256": sha256(self.layout),
                    "audited_facts":
                        NP.press_alignment_facts(alignment),
                    "rank_effect_acknowledged": True,
                },
                "normalization_method":
                    "positive_column_max_zero_if_no_positive",
            },
            "cfpb_policy": {
                "CU": "LTR_sponsor_only",
                "CV": "explicit_zero",
                "CW": "LTR_original_cosponsor_only",
                "CX": "explicit_zero",
                "accepted_bill_sponsor_original": {
                    "schema": NP.CFPB_ROUTE_SCHEMA,
                    "status": "UNSELECTED",
                    "approved_by_user": False,
                    "selected_route": None,
                    "implementation_id": None,
                },
                "accepted_bill_cosponsor": "include_in_CY",
            },
        }
        value.update(overrides)
        write_json(self.policy, value)

    def build(self) -> dict[str, object]:
        return NP.build_plan(
            accepted_ledger=self.accepted,
            directions_ledger=self.directions,
            manual_evidence=self.manual,
            layout_path=self.layout,
            policy_path=self.policy,
            baseline_manifest=self.baseline,
        )


def write_for(
    plan: dict[str, object], sheet: str, cell_range: str
) -> dict[str, object]:
    matches = [
        row
        for row in plan["writes"]
        if row["sheet"] == sheet and row["range"] == cell_range
    ]
    if len(matches) != 1:
        raise AssertionError((sheet, cell_range, len(matches)))
    return matches[0]


class NumericPlanTests(unittest.TestCase):
    def test_amendment_weight_applies_to_activity_and_efficiency(self) -> None:
        members = [{"name": "Member 000", "bioguide_id": "A000001"}]
        rows = [
            accepted_row(
                "119:hr:1",
                "Payments",
                "A000001",
                "Member 000",
                congress=119,
                role="sponsor",
                tracker_status="Passed House",
            ),
            accepted_row(
                "119:hamdt:2",
                "Payments",
                "A000001",
                "Member 000",
                congress=119,
                role="sponsor",
                tracker_status="Introduced",
            ),
        ]
        events, _ = NP.accepted_events(
            rows,
            generation_id="a" * 64,
            directions={
                ("119:hr:1", "Payments"): 1,
                ("119:hamdt:2", "Payments"): 1,
            },
            by_bioguide={"A000001": 0},
            members=members,
            cfpb_routing={},
        )
        self.assertEqual([event["decay"] for event in events], [1.0, 0.5])
        self.assertEqual(
            sum(event["sign"] * event["decay"] for event in events),
            1.5,
        )
        self.assertEqual(NP.rate(events, {"sponsor"}, 1), 66.7)

    def test_frozen_row2_weights_must_match_workbook_4_2_1_method(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            layout = json.loads(fixture.layout.read_text(encoding="utf-8"))
            layout["sublayer_legislative_weights"][
                NP.CATEGORIES[0]
            ] = [1.0, 0.0, 1.0, 0.0, 1.0]
            write_json(fixture.layout, layout)
            fixture.write_policy()
            with self.assertRaisesRegex(
                NP.NumericPlanError, "do not preserve 4/2/1"
            ):
                fixture.build()

    def test_complete_plan_recomputes_special_columns_and_all_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            plan = fixture.build()

            self.assertEqual(plan["status"], "PASS")
            self.assertEqual(len(plan["writes"]), 74)
            self.assertEqual(
                {
                    (row["sheet"], row["range"])
                    for row in plan["writes"]
                },
                RW.required_write_keys(),
            )
            cfpb = write_for(
                plan, "Legislative Score - Raw", f"CU3:DB{RAW_LAST}"
            )["values"]
            self.assertEqual(cfpb[0][:4], [0.33, 0.0, 0.0, 0.0])
            self.assertEqual(cfpb[1][:4], [0.0, 0.0, 0.66, 0.0])
            self.assertTrue(all(row[1] == 0.0 for row in cfpb))
            self.assertTrue(all(row[3] == 0.0 for row in cfpb))
            self.assertEqual(
                plan["cfpb_accepted_bill_routing"][
                    "sponsor_original_route"
                ]["status"],
                "UNSELECTED",
            )
            self.assertEqual(
                plan["audit"]["accepted"][
                    "accepted_cfpb_sponsor_original_bill_events"
                ],
                0,
            )

            tech = write_for(
                plan, "Legislative Score - Raw", f"AQ3:AX{RAW_LAST}"
            )["values"]
            mbl = write_for(
                plan, "Legislative Score - Raw", f"DS3:DZ{RAW_LAST}"
            )["values"]
            self.assertEqual(tech[2][5], 100.0)  # AV
            self.assertEqual(mbl[3][5], 100.0)  # DX
            self.assertEqual(
                plan["audit"]["manual_evidence"][
                    "stored_decay_mismatch_count"
                ],
                2,
            )
            self.assertEqual(
                plan["nonbill_numeric_policy"],
                AW.required_nonbill_numeric_policy(sha256(fixture.manual)),
            )
            self.assertEqual(
                plan["press_member_alignment"]["selected_treatment"],
                NP.PRESS_ALIGNMENT_PRESERVE_LITERAL,
            )
            self.assertEqual(
                plan["press_member_alignment"]["audited_facts"][
                    "mismatch_count"
                ],
                5,
            )

    def test_efficiency_layout_has_one_mapping_per_category(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            plan = Fixture(Path(raw)).build()
            layout = plan["efficiency_layout"]
            self.assertEqual(layout["physical_column_count"], 17)
            self.assertEqual(layout["category_count"], 18)
            self.assertEqual(
                list(layout["category_mappings"]), list(NP.CATEGORIES)
            )
            exam = layout["category_mappings"][
                "Examination & Supervision"
            ]
            capital = layout["category_mappings"]["CU Capital Requirements"]
            cu_tax = layout["category_mappings"]["CU Tax Status"]
            self.assertEqual(exam["column"], "AG")
            self.assertEqual(capital["column"], "AH")
            self.assertEqual(exam["aggregation"], "identity")
            self.assertEqual(exam["aggregate_with"], [])
            self.assertEqual(
                cu_tax["materialization"],
                "outside_legacy_efficiency_surface",
            )
            self.assertEqual(
                cu_tax["range"], NP.CU_TAX_RAW_GENERIC_RANGE
            )

    def test_plan_is_deterministic_for_same_bound_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            first = fixture.build()
            second = fixture.build()
            self.assertEqual(
                NP.canonical_bytes(first), NP.canonical_bytes(second)
            )
            self.assertEqual(
                first["computation_basis_sha256"],
                second["computation_basis_sha256"],
            )

    def test_unapproved_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            fixture.write_policy(status="PENDING", approved_by_user=False)
            with self.assertRaisesRegex(NP.NumericPlanError, "unapproved"):
                fixture.build()

    def test_missing_cu_tax_treatment_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            policy = json.loads(fixture.policy.read_text(encoding="utf-8"))
            policy["layout_policy"].pop("cu_tax_efficiency_treatment")
            write_json(fixture.policy, policy)
            with self.assertRaisesRegex(
                NP.NumericPlanError, "CU Tax efficiency treatment"
            ):
                fixture.build()

    def test_explicit_named_cu_tax_merge_is_representable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            policy = json.loads(fixture.policy.read_text(encoding="utf-8"))
            slots = policy["layout_policy"]["efficiency_slots"]
            slots[0] = {
                "column": "AG",
                "categories": [
                    "Examination & Supervision",
                    "CU Tax Status",
                ],
                "aggregation": "mean",
            }
            policy["layout_policy"]["cu_tax_efficiency_treatment"] = {
                "schema": NP.CU_TAX_TREATMENT_SCHEMA,
                "status": "APPROVED",
                "approved_by_user": True,
                "selected_treatment": NP.CU_TAX_MERGE_NAMED_SLOT,
                "omitted_category": NP.CU_TAX_CATEGORY,
                "legacy_physical_column_count": 17,
                "resolved_category_count": 18,
                "merge_target_category": "Examination & Supervision",
                "merge_target_column": "AG",
                "aggregation": "mean",
                "workbook_fact_change_acknowledged": True,
                "rank_effect_acknowledged": True,
            }
            policy["legislative_efficiency_definition"][
                "layout_resolution"
            ] = "CU Tax explicitly merged into AG with Examination"
            write_json(fixture.policy, policy)
            plan = fixture.build()
            self.assertEqual(
                plan["efficiency_layout"]["category_mappings"][
                    "CU Tax Status"
                ]["column"],
                "AG",
            )

    def test_stale_hash_bound_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            with fixture.manual.open("a", encoding="utf-8") as handle:
                handle.write("\n")
            with self.assertRaisesRegex(NP.NumericPlanError, "stale"):
                fixture.build()

    def test_missing_press_alignment_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            policy = json.loads(
                fixture.policy.read_text(encoding="utf-8")
            )
            policy["layout_policy"].pop("press_member_alignment")
            write_json(fixture.policy, policy)
            with self.assertRaisesRegex(
                NP.NumericPlanError, "press-member alignment policy"
            ):
                fixture.build()

    def test_unapproved_press_alignment_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            policy = json.loads(
                fixture.policy.read_text(encoding="utf-8")
            )
            alignment = policy["layout_policy"][
                "press_member_alignment"
            ]
            alignment["status"] = "PENDING"
            alignment["approved_by_user"] = False
            write_json(fixture.policy, policy)
            with self.assertRaisesRegex(
                NP.NumericPlanError, "press-member alignment policy"
            ):
                fixture.build()

    def test_press_alignment_facts_must_match_bound_layout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            policy = json.loads(
                fixture.policy.read_text(encoding="utf-8")
            )
            policy["layout_policy"]["press_member_alignment"][
                "audited_facts"
            ]["mismatch_count"] = 4
            write_json(fixture.policy, policy)
            with self.assertRaisesRegex(
                NP.NumericPlanError, "audited layout facts"
            ):
                fixture.build()

    def test_press_alignment_policy_must_bind_exact_layout_sha(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            policy = json.loads(
                fixture.policy.read_text(encoding="utf-8")
            )
            policy["layout_policy"]["press_member_alignment"][
                "layout_sha256"
            ] = "0" * 64
            write_json(fixture.policy, policy)
            with self.assertRaisesRegex(
                NP.NumericPlanError, "audited layout facts"
            ):
                fixture.build()

    def test_repair_choice_rejects_literal_layout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            policy = json.loads(
                fixture.policy.read_text(encoding="utf-8")
            )
            policy["layout_policy"]["press_member_alignment"][
                "selected_treatment"
            ] = NP.PRESS_ALIGNMENT_REPAIR_BY_BIOGUIDE
            write_json(fixture.policy, policy)
            with self.assertRaisesRegex(
                NP.NumericPlanError, "actually applied"
            ):
                fixture.build()

    def test_repair_choice_requires_and_accepts_audited_bioguide_join(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            layout = json.loads(
                fixture.layout.read_text(encoding="utf-8")
            )
            alignment = layout["press_literal_row_alignment"]
            alignment.update(
                {
                    "status": NP.PRESS_ALIGNMENT_REPAIRED_STATUS,
                    "member_join_applied": True,
                    "member_join_key":
                        NP.PRESS_ALIGNMENT_REPAIR_JOIN_KEY,
                    "reconstruction_semantics":
                        NP.PRESS_ALIGNMENT_REPAIR_SEMANTICS,
                }
            )
            write_json(fixture.layout, layout)
            fixture.write_policy()
            policy = json.loads(
                fixture.policy.read_text(encoding="utf-8")
            )
            policy_alignment = policy["layout_policy"][
                "press_member_alignment"
            ]
            policy_alignment["selected_treatment"] = (
                NP.PRESS_ALIGNMENT_REPAIR_BY_BIOGUIDE
            )
            policy_alignment["audited_facts"] = (
                NP.press_alignment_facts(alignment)
            )
            write_json(fixture.policy, policy)

            plan = fixture.build()
            self.assertEqual(
                plan["press_member_alignment"]["selected_treatment"],
                NP.PRESS_ALIGNMENT_REPAIR_BY_BIOGUIDE,
            )

    def test_duplicate_efficiency_category_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            layout = json.loads(fixture.layout.read_text(encoding="utf-8"))
            layout["efficiency_slots"][1]["categories"] = [
                "Examination & Supervision"
            ]
            write_json(fixture.layout, layout)
            fixture.write_policy()
            with self.assertRaisesRegex(
                NP.NumericPlanError, "each of the 18 categories exactly once"
            ):
                fixture.build()

    def test_cfpb_bill_sponsor_requires_explicit_routing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            rows = [
                json.loads(line)
                for line in fixture.accepted.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            rows.append(
                accepted_row(
                    "119:hr:99",
                    "CFPB",
                    "A000005",
                    "Member 004",
                    congress=119,
                    role="sponsor",
                    tracker_status="Passed House",
                )
            )
            write_ndjson(fixture.accepted, rows)
            direction_rows = [
                json.loads(line)
                for line in fixture.directions.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            direction_rows.append(
                {
                    "schema": NP.DIRECTION_SCHEMA,
                    "canonical_key": "119:hr:99",
                    "category": "CFPB",
                    "direction_sign": 1,
                }
            )
            write_ndjson(fixture.directions, direction_rows)
            fixture.write_policy()
            with self.assertRaisesRegex(
                NP.NumericPlanError, "explicit selected activity route"
            ):
                fixture.build()

    def test_selected_but_unimplemented_cfpb_route_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            policy = json.loads(
                fixture.policy.read_text(encoding="utf-8")
            )
            policy["cfpb_policy"]["accepted_bill_sponsor_original"] = {
                "schema": NP.CFPB_ROUTE_SCHEMA,
                "status": "SELECTED",
                "approved_by_user": True,
                "selected_route": "new_physical_surface_v1",
                "implementation_id": "numeric-plan:new-surface-v1",
            }
            write_json(fixture.policy, policy)
            with self.assertRaisesRegex(
                NP.NumericPlanError, "route is not implemented"
            ):
                fixture.build()

    def test_missing_cfpb_route_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            policy = json.loads(
                fixture.policy.read_text(encoding="utf-8")
            )
            policy["cfpb_policy"].pop(
                "accepted_bill_sponsor_original"
            )
            write_json(fixture.policy, policy)
            with self.assertRaisesRegex(
                NP.NumericPlanError, "route contract is missing"
            ):
                fixture.build()

    def test_cli_materializes_blocked_audit_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            fixture.write_policy(status="PENDING", approved_by_user=False)
            out = Path(raw) / "blocked-plan.json"
            result = NP.main(
                [
                    "--accepted-ledger",
                    str(fixture.accepted),
                    "--directions-ledger",
                    str(fixture.directions),
                    "--manual-evidence",
                    str(fixture.manual),
                    "--layout",
                    str(fixture.layout),
                    "--policy-approval",
                    str(fixture.policy),
                    "--baseline-manifest",
                    str(fixture.baseline),
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(result, 2)
            blocked = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(blocked["status"], "BLOCKED")
            self.assertEqual(blocked["writes"], [])
            self.assertFalse(blocked["production_written"])


if __name__ == "__main__":
    unittest.main()
