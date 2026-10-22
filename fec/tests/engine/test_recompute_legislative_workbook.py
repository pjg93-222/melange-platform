from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

import roster  # noqa: E402
from legislation import recompute_legislative_workbook as RW  # noqa: E402


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def receipt(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def write_ledger(root: Path, generation_id: str = "a" * 64) -> Path:
    return write_json(
        root / RW.STEP_LEDGER_NAME,
        {
            "schema": RW.STEP_LEDGER_SCHEMA,
            "status": "RUNNING",
            "staged_only": True,
            "production_written": False,
            "generation_id": generation_id,
            "completed_steps": [
                {"name": name, "outputs": {}} for name in RW.PRIOR_STEPS
            ],
        },
    )


def role_approval(
    generation_id: str,
    policy: str | None = "workbook_legacy_4_2_1",
) -> dict[str, object]:
    return {
        "schema": RW.ROLE_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "human_approved": True,
        "approved_by_user": True,
        "selected_role_weight_policy": policy,
        "selected_method_profile": RW.ROLE_METHOD_PROFILE_ID,
        "selected_policy": RW.ROLE_METHOD_PROFILE_ID,
        "consumer_methods": RW.ROLE_METHOD_PROFILE,
        "applies_to": list(RW.ROLE_METHOD_PROFILE),
        "accepted_ledger_sha256": generation_id,
    }


def numeric_plan(
    generation_id: str = "a" * 64,
    policy: str = "workbook_legacy_4_2_1",
    *,
    approved_efficiency: bool = True,
) -> dict[str, object]:
    writes = []
    for sheet, cell_range in sorted(RW.required_write_keys()):
        rows, cols = RW.range_shape(cell_range)
        writes.append(
            {
                "sheet": sheet,
                "range": cell_range,
                "values": [[0.0] * cols for _ in range(rows)],
            }
        )
    efficiency = {
        "status": "APPROVED" if approved_efficiency else "UNRESOLVED",
        "approved_by_user": approved_efficiency,
        "consistent_across_18_categories": approved_efficiency,
        "definition_id": "approved-efficiency-v1",
        "numerator": "advanced accepted actions",
        "denominator": "accepted role opportunities",
        "empty_role_handling": "explicit zero",
        "success_action_rule": "approved action ledger",
        "weighting_basis": "selected generation role/decay policy",
        "legacy_efficiency_column_count": 17,
        "resolved_category_count": 18,
        "layout_resolution": (
            "approved mean aggregation of the first two categories in AG"
        ),
    }
    slots = [
        {
            "column": RW.EFFICIENCY_COLUMNS[0],
            "categories": list(RW.CATEGORIES[:2]),
            "aggregation": "mean",
        }
    ]
    slots.extend(
        {
            "column": column,
            "categories": [category],
            "aggregation": "identity",
        }
        for column, category in zip(
            RW.EFFICIENCY_COLUMNS[1:], RW.CATEGORIES[2:]
        )
    )
    category_mappings = {}
    for slot in slots:
        for category in slot["categories"]:
            category_mappings[category] = {
                "sheet": "Sub Layer - Raw",
                "range": (
                    f"{slot['column']}4:{slot['column']}{roster.last_row('Sub Layer - Raw')}"
                ),
                "column": slot["column"],
                "aggregation": slot["aggregation"],
                "aggregate_with": [
                    item
                    for item in slot["categories"]
                    if item != category
                ],
                "source_metric": "normalized_generic_success_rate",
            }
    return {
        "schema": RW.PLAN_SCHEMA,
        "status": "PASS",
        "generation_id": generation_id,
        "selected_role_weight_policy": policy,
        "role_weight_policy_reconciled": True,
        "workbook_role_weights_verified": True,
        "all_18_categories_recomputed": True,
        "unexplained_cell_count": 0,
        "categories": list(RW.CATEGORIES),
        "legislative_efficiency_definition": efficiency,
        "efficiency_layout": {
            "raw_range": f"Sub Layer - Raw!AG4:AW{roster.last_row('Sub Layer - Raw')}",
            "normalized_range": (
                f"Sub Layer - Normalized!AG4:AW{roster.last_row('Sub Layer - Raw')}"
            ),
            "physical_column_count": 17,
            "category_count": 18,
            "slots": slots,
            "category_mappings": category_mappings,
        },
        "nonbill_numeric_policy": RW.required_nonbill_numeric_policy(
            "e" * 64
        ),
        "writes": writes,
    }


def accepted_fixture(
    root: Path,
    *,
    ledger_generation: str = "a" * 64,
    accepted_generation: str | None = None,
    policy: str | None = "workbook_legacy_4_2_1",
    reconciled: bool = True,
    approved_efficiency: bool = True,
) -> tuple[Path, dict[str, object]]:
    write_ledger(root, ledger_generation)
    generation = accepted_generation or ledger_generation
    workbook = root / RW.WORKBOOK_NAME
    workbook.write_bytes(b"not-opened-by-python")
    plan_path = write_json(
        root / "legislation_workbook_recompute_plan.json",
        numeric_plan(
            generation,
            policy or "workbook_legacy_4_2_1",
            approved_efficiency=approved_efficiency,
        ),
    )
    approval_path = write_json(
        root / "role_weight_policy_approval.json",
        role_approval(generation, policy),
    )
    value = {
        "schema": RW.ACCEPTED_RECEIPT_SCHEMA,
        "status": "PASS",
        "staged_only": True,
        "production_written": False,
        "generation_id": generation,
        "selected_role_weight_policy": policy,
        "role_weight_policy_reconciled": reconciled,
        "selected_method_profile": RW.ROLE_METHOD_PROFILE_ID,
        "consumer_role_methods": RW.ROLE_METHOD_PROFILE,
        "outputs": {RW.WORKBOOK_NAME: receipt(workbook, root)},
        "numeric_recompute_plan": receipt(plan_path, root),
        "role_weight_policy_approval": receipt(approval_path, root),
    }
    path = write_json(root / RW.ACCEPTED_RECEIPT_NAME, value)
    return path, value


def complete_artifact_verification(
    generation_id: str = "a" * 64,
) -> dict[str, object]:
    checks = []
    for sheet, cell_range in sorted(RW.required_write_keys()):
        rows, cols = RW.range_shape(cell_range)
        checks.append(
            {
                "sheet": sheet,
                "range": cell_range,
                "table_record_count": 1,
                "expected_rows": rows,
                "expected_cols": cols,
                "expected_cells": rows * cols,
                "actual_rows": rows,
                "actual_cols": cols,
                "actual_cells": rows * cols,
                "count_match": True,
                "non_numeric_cells": 0,
            }
        )
    expected_cells = sum(item["expected_cells"] for item in checks)
    members = RW.EXPECTED_PILLAR_MEMBER_COUNT
    return {
        "schema": "legislation-workbook-artifact-tool-verification-v1",
        "status": "PASS",
        "engine": "@oai/artifact-tool",
        "generation_id": generation_id,
        "formula_error_count": 0,
        "non_numeric_cells": 0,
        "pillar_snapshot_invalid_cells": 0,
        "output_written": True,
        "numeric_checks": checks,
        "critical_inspection_expected_count": len(checks),
        "critical_inspection_actual_count": len(checks),
        "critical_inspection_exact_count_passes": len(checks),
        "critical_inspection_count_mismatches": 0,
        "critical_inspection_expected_cells": expected_cells,
        "critical_inspection_actual_cells": expected_cells,
        "blended_inspection": {
            "range": f"A3:O{roster.last_row('Blended Model')}",
            "table_record_count": 1,
            "expected_rows": members,
            "expected_cols": 15,
            "expected_cells": members * 15,
            "actual_rows": members,
            "actual_cols": 15,
            "actual_cells": members * 15,
            "count_match": True,
        },
        "pillar_snapshot_expected_members": members,
        "pillar_snapshot_members": members,
        "pillar_snapshot_member_count_match": True,
        "pillar_snapshot_field_counts": {
            "O": members,
            "E": members,
            "F": members,
        },
        "pillar_snapshot_unnamed_rows": 0,
        "pillar_snapshot_duplicate_names": [],
    }


class WorkbookRecomputeTests(unittest.TestCase):
    def test_source_contract_uses_only_artifact_tool_for_xlsx(self) -> None:
        python = (FEC / "legislation" / "recompute_legislative_workbook.py").read_text(
            encoding="utf-8"
        )
        helper = (
            FEC / "recompute_legislative_workbook_artifact.cjs"
        ).read_text(encoding="utf-8")
        self.assertNotIn("openpyxl", python)
        self.assertNotIn("xlsxwriter", python)
        self.assertIn('require("@oai/artifact-tool")', helper)
        self.assertIn("SpreadsheetFile.importXlsx", helper)
        self.assertIn("SpreadsheetFile.exportXlsx", helper)
        self.assertIn("ARTIFACT_TOOL_NODE", python)
        self.assertIn("ARTIFACT_TOOL_NODE_MODULES", python)
        self.assertNotIn("OAI_ARTIFACT_TOOL_NODE", python)
        self.assertNotIn("os.symlink", python)
        self.assertIn("critical_inspection_count_mismatches", helper)
        self.assertIn("pillar_snapshot_expected_members", helper)
        self.assertNotIn("Object.keys(snapshot.O).length > 0", helper)

    def test_exact_artifact_inspection_counts_pass(self) -> None:
        verification = complete_artifact_verification()
        summary = RW.validate_artifact_tool_verification(
            verification,
            generation_id="a" * 64,
        )
        self.assertEqual(summary["critical_inspection_count_mismatches"], 0)
        self.assertEqual(
            summary["pillar_snapshot_members"],
            RW.EXPECTED_PILLAR_MEMBER_COUNT,
        )

    def test_truncated_critical_table_blocks_even_when_nonnumeric_is_zero(
        self,
    ) -> None:
        verification = complete_artifact_verification()
        item = verification["numeric_checks"][0]
        item["actual_rows"] -= 1
        item["actual_cells"] -= item["actual_cols"]
        item["count_match"] = False
        verification["critical_inspection_count_mismatches"] = 1
        verification["critical_inspection_exact_count_passes"] -= 1
        verification["critical_inspection_actual_cells"] -= item[
            "actual_cols"
        ]
        with self.assertRaisesRegex(
            RW.WorkbookRecomputeError, "exact-count gate"
        ):
            RW.validate_artifact_tool_verification(
                verification,
                generation_id="a" * 64,
            )

    def test_missing_critical_table_and_short_pillar_snapshot_block(
        self,
    ) -> None:
        verification = complete_artifact_verification()
        verification["numeric_checks"].pop()
        verification["critical_inspection_actual_count"] -= 1
        verification["pillar_snapshot_members"] -= 1
        verification["pillar_snapshot_member_count_match"] = False
        verification["pillar_snapshot_field_counts"]["O"] -= 1
        with self.assertRaisesRegex(
            RW.WorkbookRecomputeError, "exact-count gate"
        ):
            RW.validate_artifact_tool_verification(
                verification,
                generation_id="a" * 64,
            )

    def test_step_ledger_generation_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = accepted_fixture(
                root,
                ledger_generation="a" * 64,
                accepted_generation="b" * 64,
            )
            ledger = RW.validate_step_ledger(root)
            with self.assertRaisesRegex(
                RW.WorkbookRecomputeError, "generation-bound"
            ):
                RW.validate_accepted_receipt(root, ledger, path)

    def test_role_policy_must_be_selected_and_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = accepted_fixture(
                root, policy=None, reconciled=False
            )
            ledger = RW.validate_step_ledger(root)
            with self.assertRaisesRegex(
                RW.WorkbookRecomputeError, "unselected/unreconciled"
            ):
                RW.validate_accepted_receipt(root, ledger, path)

    def test_unresolved_efficiency_definition_blocks_before_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = accepted_fixture(
                root, approved_efficiency=False
            )
            ledger = RW.validate_step_ledger(root)
            with self.assertRaisesRegex(
                RW.WorkbookRecomputeError, "efficiency definition"
            ):
                RW.validate_accepted_receipt(root, ledger, path)

    def test_complete_plan_covers_exact_18_blocks_and_sublayers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = accepted_fixture(root)
            ledger = RW.validate_step_ledger(root)
            accepted = RW.validate_accepted_receipt(root, ledger, path)
            plan = accepted["validated_plan"]
            self.assertEqual(plan["categories"], list(RW.CATEGORIES))
            self.assertEqual(
                {
                    (item["sheet"], item["range"])
                    for item in plan["writes"]
                },
                RW.required_write_keys(),
            )
            self.assertTrue(
                plan["legislative_efficiency_definition"][
                    "consistent_across_18_categories"
                ]
            )

    def test_missing_or_extra_matrix_range_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_ledger(root)
            workbook = root / RW.WORKBOOK_NAME
            workbook.write_bytes(b"xlsx-placeholder")
            plan = numeric_plan()
            plan["writes"] = plan["writes"][:-1]
            plan_path = write_json(root / "plan.json", plan)
            accepted = {
                "schema": RW.ACCEPTED_RECEIPT_SCHEMA,
                "status": "PASS",
                "staged_only": True,
                "production_written": False,
                "generation_id": "a" * 64,
                "selected_role_weight_policy": "workbook_legacy_4_2_1",
                "role_weight_policy_reconciled": True,
                "selected_method_profile": RW.ROLE_METHOD_PROFILE_ID,
                "consumer_role_methods": RW.ROLE_METHOD_PROFILE,
                "outputs": {RW.WORKBOOK_NAME: receipt(workbook, root)},
                "numeric_recompute_plan": receipt(plan_path, root),
            }
            approval_path = write_json(
                root / "role_weight_policy_approval.json",
                role_approval("a" * 64),
            )
            accepted["role_weight_policy_approval"] = receipt(
                approval_path, root
            )
            accepted_path = write_json(
                root / RW.ACCEPTED_RECEIPT_NAME, accepted
            )
            ledger = RW.validate_step_ledger(root)
            with self.assertRaisesRegex(
                RW.WorkbookRecomputeError, "range coverage differs"
            ):
                RW.validate_accepted_receipt(root, ledger, accepted_path)

    def test_efficiency_layout_cannot_hide_eighteenth_category(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_ledger(root)
            workbook = root / RW.WORKBOOK_NAME
            workbook.write_bytes(b"xlsx-placeholder")
            plan = numeric_plan()
            plan["efficiency_layout"]["category_mappings"].pop(
                RW.CATEGORIES[-1]
            )
            plan_path = write_json(root / "plan.json", plan)
            approval_path = write_json(
                root / "role_weight_policy_approval.json",
                role_approval("a" * 64),
            )
            accepted = {
                "schema": RW.ACCEPTED_RECEIPT_SCHEMA,
                "status": "PASS",
                "staged_only": True,
                "production_written": False,
                "generation_id": "a" * 64,
                "selected_role_weight_policy": "workbook_legacy_4_2_1",
                "role_weight_policy_reconciled": True,
                "selected_method_profile": RW.ROLE_METHOD_PROFILE_ID,
                "consumer_role_methods": RW.ROLE_METHOD_PROFILE,
                "outputs": {RW.WORKBOOK_NAME: receipt(workbook, root)},
                "numeric_recompute_plan": receipt(plan_path, root),
                "role_weight_policy_approval": receipt(
                    approval_path, root
                ),
            }
            accepted_path = write_json(
                root / RW.ACCEPTED_RECEIPT_NAME, accepted
            )
            ledger = RW.validate_step_ledger(root)
            with self.assertRaisesRegex(
                RW.WorkbookRecomputeError, "mappings are incomplete"
            ):
                RW.validate_accepted_receipt(root, ledger, accepted_path)

    def test_old_frozen_nonbill_numeric_policy_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_ledger(root)
            workbook = root / RW.WORKBOOK_NAME
            workbook.write_bytes(b"xlsx-placeholder")
            plan = numeric_plan()
            plan["nonbill_numeric_policy"] = {
                "cfpb_sign_on_letters": {
                    "action": "preserve_frozen_baseline_values"
                }
            }
            plan_path = write_json(root / "plan.json", plan)
            approval_path = write_json(
                root / "role_weight_policy_approval.json",
                role_approval("a" * 64),
            )
            accepted = {
                "schema": RW.ACCEPTED_RECEIPT_SCHEMA,
                "status": "PASS",
                "staged_only": True,
                "production_written": False,
                "generation_id": "a" * 64,
                "selected_role_weight_policy": "workbook_legacy_4_2_1",
                "role_weight_policy_reconciled": True,
                "selected_method_profile": RW.ROLE_METHOD_PROFILE_ID,
                "consumer_role_methods": RW.ROLE_METHOD_PROFILE,
                "outputs": {RW.WORKBOOK_NAME: receipt(workbook, root)},
                "numeric_recompute_plan": receipt(plan_path, root),
                "role_weight_policy_approval": receipt(
                    approval_path, root
                ),
            }
            accepted_path = write_json(
                root / RW.ACCEPTED_RECEIPT_NAME, accepted
            )
            ledger = RW.validate_step_ledger(root)
            with self.assertRaisesRegex(
                RW.WorkbookRecomputeError,
                "manual-evidence|recompute CU/CW",
            ):
                RW.validate_accepted_receipt(root, ledger, accepted_path)


HELPER = FEC / "recompute_legislative_workbook_artifact.cjs"
FAKE_ARTIFACT_TOOL = FEC / "tests" / "fixtures" / "fake_artifact_tool.cjs"
GEOMETRY_SHEETS = (
    "Legislative Score - Raw",
    "Legislative Score - Normalized",
    "Sub Layer - Raw",
    "Sub Layer - Normalized",
    "Blended Model",
)


def geometry_for(count: int) -> dict[str, object]:
    return {
        "member_count": count,
        "sheets": {
            sheet: {
                "first_row": roster.first_row(sheet),
                "last_row": roster.first_row(sheet) + count - 1,
            }
            for sheet in GEOMETRY_SHEETS
        },
    }


def rows_of(cell_range: str) -> int:
    first, last = cell_range.split(":")
    digits = lambda cell: int("".join(c for c in cell if c.isdigit()))  # noqa: E731
    return digits(last) - digits(first) + 1


@unittest.skipUnless(shutil.which("node"), "node is required to run the helper")
class ArtifactHelperGeometryTests(unittest.TestCase):
    """The helper takes its member count and rows from the plan its Python caller writes (the roster), never from literals."""

    def run_helper(self, geometry: object):
        plan = {
            "schema": RW.PLAN_SCHEMA,
            "status": "PASS",
            "generation_id": "g" * 64,
            "selected_role_weight_policy": "workbook_legacy_4_2_1",
            "writes": [
                {"sheet": "Legislative Score - Raw", "range": "C3:C3", "values": [[1]]}
            ],
        }
        if geometry is not None:
            plan["geometry"] = geometry
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            fake = tmp / "node_modules" / "@oai" / "artifact-tool"
            fake.mkdir(parents=True)
            shutil.copyfile(FAKE_ARTIFACT_TOOL, fake / "index.js")
            helper = tmp / HELPER.name
            shutil.copyfile(HELPER, helper)
            workbook = tmp / "input.xlsx"
            workbook.write_bytes(b"fake workbook")
            plan_path = write_json(tmp / "plan.json", plan)
            output, snapshot = tmp / "out.xlsx", tmp / "snapshot.json"
            report, log = tmp / "report.json", tmp / "calls.log"
            done = subprocess.run(
                [
                    "node", str(helper), str(workbook), str(plan_path),
                    str(output), str(snapshot), str(report),
                ],
                cwd=tmp,
                env={**os.environ, "FAKE_ARTIFACT_TOOL_LOG": str(log)},
                capture_output=True,
                text=True,
                timeout=120,
            )
            load = lambda p: json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None  # noqa: E731
            calls = (
                [json.loads(line) for line in log.read_text().splitlines()]
                if log.is_file()
                else []
            )
            return done, load(report), load(snapshot), calls

    def test_ranges_follow_the_member_count_it_is_given(self) -> None:
        for count in sorted({roster.count(), 533, 535}):
            with self.subTest(count=count):
                done, report, snapshot, calls = self.run_helper(geometry_for(count))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["pillar_snapshot_expected_members"], count)
                self.assertEqual(report["pillar_snapshot_members"], count)
                self.assertEqual(len(snapshot["O"]), count)
                self.assertEqual(
                    report["blended_inspection"]["range"], f"A3:O{count + 2}"
                )
                checks = {(c["sheet"], c["range"]) for c in report["numeric_checks"]}
                for sheet in ("Legislative Score - Raw", "Legislative Score - Normalized"):
                    self.assertIn((sheet, f"C3:J{count + 2}"), checks)
                    self.assertIn((sheet, f"EW3:FD{count + 2}"), checks)
                for sheet in ("Sub Layer - Raw", "Sub Layer - Normalized"):
                    self.assertIn((sheet, f"C4:C{count + 3}"), checks)
                    self.assertIn((sheet, f"U4:U{count + 3}"), checks)
                    self.assertIn((sheet, f"AG4:AW{count + 3}"), checks)
                self.assertEqual(len(checks), 18 * 2 + 18 * 2 + 2)
                for call in calls:
                    if call["op"] == "inspect" and call["kind"] == "table":
                        self.assertGreaterEqual(
                            call["tableMaxRows"], rows_of(call["range"]), call
                        )

    def test_python_accepts_the_helper_report_for_its_own_geometry(self) -> None:
        done, report, _, _ = self.run_helper(RW.helper_geometry())
        self.assertEqual(done.returncode, 0, done.stderr)
        summary = RW.validate_artifact_tool_verification(
            report, generation_id="g" * 64
        )
        self.assertEqual(summary["pillar_snapshot_members"], roster.count())

    def test_invoke_helper_hands_the_roster_geometry_to_the_helper(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fake = root / "fake_modules" / "@oai" / "artifact-tool"
            fake.mkdir(parents=True)
            shutil.copyfile(FAKE_ARTIFACT_TOOL, fake / "index.js")
            workbook = root / "input.xlsx"
            workbook.write_bytes(b"fake workbook")
            plan = {
                "schema": RW.PLAN_SCHEMA,
                "status": "PASS",
                "generation_id": "g" * 64,
                "selected_role_weight_policy": "workbook_legacy_4_2_1",
                "writes": [
                    {"sheet": "Legislative Score - Raw", "range": "C3:C3", "values": [[1]]}
                ],
            }
            result = RW.invoke_helper(
                node=Path(shutil.which("node")),
                modules=root / "fake_modules",
                root=root,
                accepted={"workbook_path": workbook, "generation_id": "g" * 64},
                validated_plan=plan,
                suffix="geometry",
                render=False,
            )
            sent = json.loads(result["plan"].read_text(encoding="utf-8"))
            self.assertEqual(sent["geometry"], RW.helper_geometry())
            self.assertNotIn("geometry", plan)
            self.assertEqual(
                result["exact_count_verification"]["pillar_snapshot_members"],
                roster.count(),
            )

    def test_helper_geometry_is_the_roster(self) -> None:
        geometry = RW.helper_geometry()
        self.assertEqual(geometry["member_count"], roster.count())
        self.assertEqual(set(geometry["sheets"]), set(GEOMETRY_SHEETS))
        for sheet, rows in geometry["sheets"].items():
            self.assertEqual(rows["first_row"], roster.first_row(sheet))
            self.assertEqual(rows["last_row"], roster.last_row(sheet))

    def test_helper_refuses_missing_or_inconsistent_geometry(self) -> None:
        good = geometry_for(535)
        short = geometry_for(535)
        short["sheets"]["Blended Model"]["last_row"] -= 1
        no_sheet = geometry_for(535)
        del no_sheet["sheets"]["Sub Layer - Normalized"]
        cases = {
            "missing": None,
            "not an object": "535",
            "zero members": {**good, "member_count": 0},
            "string count": {**good, "member_count": "535"},
            "last row off by one": short,
            "sheet absent": no_sheet,
        }
        for label, geometry in cases.items():
            with self.subTest(label):
                done, report, snapshot, calls = self.run_helper(geometry)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("roster geometry", done.stderr)
                self.assertIsNone(report)
                self.assertIsNone(snapshot)
                self.assertEqual(calls, [])

    def test_helper_source_has_no_member_count_literals(self) -> None:
        import re

        text = HELPER.read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"\b53\d\b", text), [])


if __name__ == "__main__":
    unittest.main()
