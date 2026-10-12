from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from legislation import build_legislation_workbook_numeric_plan as NP  # noqa: E402
from legislation import extract_legislation_workbook_audited_layout as EL  # noqa: E402


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_minimal_semantic_xlsx(
    path: Path,
    *,
    formula_cache: str,
    numeric_value: str = "1.0",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_rows = []
    relationships = []
    for index, name in enumerate(EL.AUDITED_SEMANTIC_SHEETS, start=1):
        sheet_rows.append(
            f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>'
        )
        relationships.append(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/'
            f'2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships"><sheets>'
        + "".join(sheet_rows)
        + "</sheets></workbook>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships">'
        + "".join(relationships)
        + "</Relationships>"
    )
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" count="1" uniqueCount="1">'
        "<si><t>member</t></si></sst>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData><row r="1">'
        '<c r="A1" t="s"><v>0</v></c>'
        f'<c r="B1"><v>{numeric_value}</v></c>'
        f'<c r="C1"><f>SUM(B1:B1)</f><v>{formula_cache}</v></c>'
        "</row></sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("xl/workbook.xml", workbook)
        package.writestr("xl/_rels/workbook.xml.rels", rels)
        package.writestr("xl/sharedStrings.xml", shared)
        for index in range(1, len(EL.AUDITED_SEMANTIC_SHEETS) + 1):
            package.writestr(f"xl/worksheets/sheet{index}.xml", sheet)
    return path


def payload(workbook_sha: str) -> dict[str, object]:
    members = [
        {
            "name": f"Member {index:03d}",
            "bioguide": f"A{index + 1:06d}",
            "bioguide_id": f"A{index + 1:06d}",
            "raw_row": index + 3,
            "sublayer_row": index + 4,
            "congestion_row": 535 - index,
        }
        for index in range(533)
    ]
    slots = [
        {
            "column": column,
            "status": (
                "IDENTIFIED_FROM_WORKBOOK" if index < 16 else "UNRESOLVED"
            ),
            "category_candidates": (
                [EL.CATEGORIES[index]] if index < 16 else []
            ),
            "evidence": {
                "formula_count": 533,
                "table_basis_counts": {},
                "referenced_normalized_column_counts": {},
            },
        }
        for index, column in enumerate(EL.EFFICIENCY_COLUMNS)
    ]
    return {
        "schema": EL.HELPER_SCHEMA,
        "status": "PASS",
        "engine": "@oai/artifact-tool",
        "production_written": False,
        "source_workbook_sha256": workbook_sha,
        "source_workbook_sha256_after": workbook_sha,
        "raw_block_ranges": EL.BLOCK_RANGES,
        "sublayer_category_columns": EL.CATEGORY_SUBLAYER_COLUMNS,
        "members": members,
        "sublayer_nonlegislative_values": {
            category: [float(index)] * 533
            for index, category in enumerate(EL.CATEGORIES)
        },
        "sublayer_legislative_weights": {
            category: [2 / 7, 1 / 7, 2 / 7, 1 / 7, 1 / 7]
            for category in EL.CATEGORIES
        },
        "efficiency_slots": slots,
        "sublayer_formula_audit": {
            "checked_cells": 533 * 18,
            "mismatch_count": 0,
            "mismatches": [],
        },
        "formula_error_scan": {
            "status": "PASS",
            "match_count": 0,
            "matches": [],
        },
        "renders": [
            {
                "sheet": sheet,
                "range": cell_range,
                "file": f"sample-{index}.png",
                "size": 10 + index,
                "sha256": f"{index + 1:064x}",
            }
            for index, (sheet, cell_range) in enumerate(
                [
                    ("Legislative Score - Raw", "A1:FD12"),
                    ("Sub Layer - Raw", "A1:AW16"),
                    ("Congestion", "A1:Q16"),
                ]
            )
        ],
        "workbook_inspections": [],
    }


class ExtractorTests(unittest.TestCase):
    def test_constants_match_numeric_plan_consumer_exactly(self) -> None:
        self.assertEqual(EL.CATEGORIES, NP.CATEGORIES)
        self.assertEqual(EL.BLOCK_RANGES, NP.BLOCK_RANGES)
        self.assertEqual(
            EL.CATEGORY_SUBLAYER_COLUMNS, NP.CATEGORY_SUBLAYER_COLUMNS
        )
        self.assertEqual(EL.EFFICIENCY_COLUMNS, NP.EFFICIENCY_COLUMNS)

    def test_frozen_baseline_only_and_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline = root / "baseline"
            frozen = baseline / "files" / EL.WORKBOOK_BASENAME
            frozen.parent.mkdir(parents=True)
            frozen.write_bytes(b"frozen workbook")
            production = root / EL.WORKBOOK_BASENAME
            production.write_bytes(b"live workbook")
            manifest = write_json(
                baseline / "baseline_manifest.json",
                {
                    "schema": EL.BASELINE_SCHEMA,
                    "immutable_existing_copy_policy": True,
                    "source_root": str(root),
                    "files": [
                        {
                            "source": str(production),
                            "copy": str(frozen),
                            "size": frozen.stat().st_size,
                            "sha256": sha256(frozen),
                        }
                    ],
                },
            )
            result = EL.resolve_frozen_workbook(manifest)
            self.assertEqual(result["workbook_path"], frozen.resolve())
            self.assertEqual(
                result["workbook_receipt"]["sha256"], sha256(frozen)
            )
            broken = json.loads(manifest.read_text(encoding="utf-8"))
            broken["files"][0]["copy"] = str(production)
            write_json(manifest, broken)
            with self.assertRaisesRegex(
                EL.LayoutExtractionError,
                "production workbook|outside the baseline",
            ):
                EL.resolve_frozen_workbook(manifest)

    def test_stage_rejects_production_and_immutable_input_directories(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline_dir = root / "baseline"
            frozen = baseline_dir / "files" / EL.WORKBOOK_BASENAME
            frozen.parent.mkdir(parents=True)
            frozen.write_bytes(b"frozen workbook")
            production = root / EL.WORKBOOK_BASENAME
            production.write_bytes(b"live workbook")
            manifest = write_json(
                baseline_dir / "baseline_manifest.json",
                {
                    "schema": EL.BASELINE_SCHEMA,
                    "immutable_existing_copy_policy": True,
                    "source_root": str(root),
                    "files": [
                        {
                            "source": str(production),
                            "copy": str(frozen),
                            "size": frozen.stat().st_size,
                            "sha256": sha256(frozen),
                        }
                    ],
                },
            )
            baseline = EL.resolve_frozen_workbook(manifest)
            with self.assertRaisesRegex(
                EL.LayoutExtractionError, "production root"
            ):
                EL.assert_stage_safe(root, baseline=baseline)
            with self.assertRaisesRegex(
                EL.LayoutExtractionError, "immutable baseline"
            ):
                EL.assert_stage_safe(frozen.parent, baseline=baseline)

            safe_stage = root / "audits" / "layout-stage"
            with mock.patch.dict(
                os.environ,
                {
                    "ARTIFACT_TOOL_NODE": "",
                    "ARTIFACT_TOOL_NODE_MODULES": "",
                },
                clear=False,
            ):
                result = EL.main(
                    [
                        "--baseline-manifest",
                        str(manifest),
                        "--stage-dir",
                        str(safe_stage),
                    ]
                )
            self.assertEqual(result, 2)
            blocked = json.loads(
                (safe_stage / EL.DEFAULT_LAYOUT_NAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(blocked["status"], "BLOCKED")
            self.assertFalse(blocked["production_written"])

            unsafe_receipt = root / EL.DEFAULT_LAYOUT_NAME
            result = EL.main(
                [
                    "--baseline-manifest",
                    str(manifest),
                    "--stage-dir",
                    str(root),
                ]
            )
            self.assertEqual(result, 2)
            self.assertFalse(unsafe_receipt.exists())

    def test_payload_requires_all_members_ranges_values_and_weights(self) -> None:
        workbook_sha = "a" * 64
        valid = payload(workbook_sha)
        normalized = EL.validate_helper_payload(
            valid, expected_sha256=workbook_sha
        )
        self.assertEqual(len(normalized["members"]), 533)
        self.assertEqual(
            normalized["members"][0]["bioguide"],
            normalized["members"][0]["bioguide_id"],
        )
        self.assertEqual(
            set(normalized["sublayer_nonlegislative_values"]),
            set(EL.CATEGORIES),
        )
        self.assertTrue(
            all(
                len(values) == 533
                for values in normalized[
                    "sublayer_nonlegislative_values"
                ].values()
            )
        )
        self.assertTrue(
            all(
                len(values) == 5
                for values in normalized[
                    "sublayer_legislative_weights"
                ].values()
            )
        )

        invalid = json.loads(json.dumps(valid))
        invalid["members"].pop()
        with self.assertRaisesRegex(
            EL.LayoutExtractionError, "exactly 533 members"
        ):
            EL.validate_helper_payload(
                invalid, expected_sha256=workbook_sha
            )

    def test_formula_errors_or_unverified_sublayers_fail_closed(self) -> None:
        workbook_sha = "b" * 64
        invalid = payload(workbook_sha)
        invalid["formula_error_scan"]["status"] = "BLOCKED"
        invalid["formula_error_scan"]["match_count"] = 1
        with self.assertRaisesRegex(EL.LayoutExtractionError, "error scan"):
            EL.validate_helper_payload(
                invalid, expected_sha256=workbook_sha
            )

        invalid = payload(workbook_sha)
        invalid["sublayer_formula_audit"]["mismatch_count"] = 1
        with self.assertRaisesRegex(
            EL.LayoutExtractionError, "9,594 category"
        ):
            EL.validate_helper_payload(
                invalid, expected_sha256=workbook_sha
            )

    def test_unresolved_efficiency_slot_is_preserved_not_invented(self) -> None:
        workbook_sha = "c" * 64
        raw_payload = payload(workbook_sha)
        with tempfile.TemporaryDirectory() as raw:
            receipt_file = Path(raw) / "raw.json"
            write_json(receipt_file, raw_payload)
            baseline = {
                "manifest_receipt": {
                    "path": "baseline.json",
                    "size": 1,
                    "sha256": "d" * 64,
                },
                "workbook_receipt": {
                    "path": "frozen.xlsx",
                    "size": 1,
                    "sha256": workbook_sha,
                },
                "production_source_declared": "production.xlsx",
            }
            layout = EL.build_layout(
                raw_payload,
                baseline=baseline,
                raw_extraction_receipt=EL.file_receipt(receipt_file),
            )
        self.assertEqual(layout["schema"], NP.LAYOUT_SCHEMA)
        self.assertEqual(layout["status"], "PASS")
        self.assertFalse(layout["numeric_plan_ready"])
        self.assertEqual(
            layout["efficiency_mapping_18_to_17"]["status"], "UNRESOLVED"
        )
        self.assertFalse(
            layout["efficiency_mapping_18_to_17"]["approved_by_user"]
        )
        self.assertEqual(
            layout["efficiency_slots"][-1]["categories"], []
        )
        self.assertIsNone(
            layout["efficiency_slots"][-1]["aggregation"]
        )

    def test_exact_17_slot_mapping_surfaces_cu_tax_omission(self) -> None:
        workbook_sha = "d" * 64
        raw_payload = payload(workbook_sha)
        raw_payload["efficiency_slots"] = [
            {
                "column": column,
                "status": "IDENTIFIED_FROM_WORKBOOK",
                "category_candidates": [
                    EL.EFFICIENCY_CATEGORY_BY_COLUMN[column]
                ],
                "evidence": {
                    "formula_count": 533,
                    "literal_formula_rows_verified": True,
                },
            }
            for column in EL.EFFICIENCY_COLUMNS
        ]
        with tempfile.TemporaryDirectory() as raw:
            receipt_file = Path(raw) / "raw.json"
            write_json(receipt_file, raw_payload)
            layout = EL.build_layout(
                raw_payload,
                baseline={
                    "manifest_receipt": {
                        "path": "baseline.json",
                        "size": 1,
                        "sha256": "e" * 64,
                    },
                    "workbook_receipt": {
                        "path": "frozen.xlsx",
                        "size": 1,
                        "sha256": workbook_sha,
                    },
                    "production_source_declared": "production.xlsx",
                },
                raw_extraction_receipt=EL.file_receipt(receipt_file),
            )
        mapping = layout["efficiency_mapping_18_to_17"]
        self.assertEqual(mapping["status"], "UNRESOLVED_MISSING_CU_TAX")
        self.assertEqual(mapping["workbook_identified_slot_count"], 17)
        self.assertEqual(mapping["unresolved_slots"], [])
        self.assertEqual(mapping["omitted_categories"], ["CU Tax Status"])
        self.assertFalse(mapping["inferred_merge"])

    def test_six_sheet_semantics_ignore_formula_cache_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline = write_minimal_semantic_xlsx(
                root / "baseline.xlsx", formula_cache="1"
            )
            calibration = write_minimal_semantic_xlsx(
                root / "calibration.xlsx", formula_cache="#VALUE!"
            )
            receipt = EL.verify_six_sheet_semantic_equality(
                baseline, calibration
            )
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["sheet_count"], 6)
            self.assertEqual(
                receipt["ignored"], ["formula_cached_values"]
            )
            self.assertTrue(
                all(
                    row["equal"] is True
                    for row in receipt["sheets"].values()
                )
            )

            changed = write_minimal_semantic_xlsx(
                root / "changed.xlsx",
                formula_cache="#VALUE!",
                numeric_value="2",
            )
            with self.assertRaisesRegex(
                EL.LayoutExtractionError, "canonically identical"
            ):
                EL.verify_six_sheet_semantic_equality(
                    baseline, changed
                )

    def test_exact_formula_builders_preserve_literal_rows(self) -> None:
        press = EL._expected_press_normalization_formula("Payments")
        self.assertIn("Table10[[#This Row],[Payments]]", press)
        self.assertIn("_xlpm.x/_xlpm.negMax", press)
        self.assertEqual(
            EL._expected_sublayer_formula(
                legislative_first="AI",
                legislative_last="AM",
                press_column="I",
                sublayer_row=25,
            ),
            "=SUMPRODUCT('Legislative Score - Normalized'!"
            "AI24:AM24,'Legislative Score - Normalized'!"
            "AI$2:AM$2) + SUMPRODUCT('Press Release - Normalized'!"
            "I23,'Press Release - Normalized'!I$535)",
        )

    def test_artifact_runtime_uses_required_env_and_node_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            node = root / ("node.exe" if os.name == "nt" else "node")
            node.write_bytes(b"node")
            modules = root / "modules"
            modules.mkdir()
            runtime = EL.artifact_runtime(
                {
                    "ARTIFACT_TOOL_NODE": str(node),
                    "ARTIFACT_TOOL_NODE_MODULES": str(modules),
                    "NODE_PATH": "existing-modules",
                }
            )
            self.assertEqual(runtime["node"], node.resolve())
            self.assertEqual(runtime["modules"], modules.resolve())
            self.assertEqual(
                runtime["environment"]["NODE_PATH"],
                str(modules.resolve())
                + os.pathsep
                + "existing-modules",
            )

    def test_zero_legislation_calibration_is_exactly_source_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline_dir = root / "baseline"
            frozen = baseline_dir / "files" / EL.WORKBOOK_BASENAME
            frozen.parent.mkdir(parents=True)
            frozen.write_bytes(b"frozen")
            manifest = write_json(
                baseline_dir / "baseline_manifest.json",
                {"schema": EL.BASELINE_SCHEMA, "files": []},
            )
            smoke_dir = root / "smoke"
            output = smoke_dir / "smoke_output_workbook.xlsx"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"materialized")
            model_sheets = {
                sheet: [[f"column-{index}" for index in range(13)]]
                for sheet in EL.CALIBRATION_MODEL_SHEETS
            }
            payload_path = write_json(
                smoke_dir / "artifact_tool_payload.json",
                {
                    "schema": EL.CALIBRATION_PAYLOAD_SCHEMA,
                    "production_written": False,
                    "generation_id": "0" * 64,
                    "baseline": {"model": str(frozen)},
                    "outputs": {"model": str(output)},
                    "model_sheets": model_sheets,
                },
            )
            mutations = [
                {
                    "workbook": "model",
                    "sheet": sheet,
                    "address": "A1:M1",
                    "rows": 1,
                    "cols": 13,
                    "actual_error_token_count": 0,
                }
                for sheet in sorted(EL.CALIBRATION_MODEL_SHEETS)
            ]
            mutations.append(
                {
                    "workbook": "archive",
                    "sheet": EL.CALIBRATION_ARCHIVE_SHEET,
                    "address": "A1:M1",
                    "rows": 1,
                    "cols": 13,
                    "actual_error_token_count": 0,
                }
            )
            report_path = write_json(
                smoke_dir / "inspection.json",
                {
                    "schema": EL.CALIBRATION_REPORT_SCHEMA,
                    "status": "PASS",
                    "engine": "@oai/artifact-tool",
                    "staged_only": True,
                    "production_written": False,
                    "generation_id": "0" * 64,
                    "required_sheets_present": True,
                    "targeted_formula_error_count": 0,
                    "mutations": mutations,
                    "outputs": {"model": str(output)},
                },
            )
            previews = []
            for index in range(3):
                preview = smoke_dir / f"preview-{index}.png"
                preview.write_bytes(f"png-{index}".encode())
                previews.append(
                    {
                        "path": str(preview),
                        "size": preview.stat().st_size,
                        "sha256": sha256(preview),
                    }
                )
            capability = write_json(
                smoke_dir / "capability.json",
                {
                    "schema": EL.CAPABILITY_SCHEMA,
                    "status": "PASS",
                    "approved_engine": "@oai/artifact-tool",
                    "staged_only": True,
                    "production_written": False,
                    "baseline_input_sha256": sha256(frozen),
                    "producer_sha256": sha256(
                        FEC / "legislation" / "build_accepted_legislation_workbook.py"
                    ),
                    "helper_sha256": sha256(
                        FEC / "build_accepted_legislation_workbook.cjs"
                    ),
                    "frozen_baseline_manifest": {
                        "path": str(manifest),
                        "size": manifest.stat().st_size,
                        "sha256": sha256(manifest),
                    },
                    "output_workbook": {
                        "path": str(output),
                        "size": output.stat().st_size,
                        "sha256": sha256(output),
                    },
                    "inspection": {
                        "path": str(report_path),
                        "size": report_path.stat().st_size,
                        "sha256": sha256(report_path),
                    },
                    "previews": previews,
                    "smoke_test": {
                        "passed": True,
                        "composite_rebind": True,
                        "helper_source_unchanged": True,
                        "required_sheets_present": True,
                        "targeted_formula_error_count": 0,
                        "source_contract_tests": {
                            "passed": True,
                            "tests_run": 1,
                        },
                    },
                },
            )
            baseline = {
                "manifest_receipt": EL.file_receipt(manifest),
                "workbook_path": frozen.resolve(),
                "workbook_receipt": EL.file_receipt(frozen),
            }
            value = EL.resolve_zero_legislation_calibration(
                capability, baseline=baseline
            )
            self.assertEqual(
                value["workbook_receipt"]["sha256"], sha256(output)
            )
            self.assertEqual(
                value["proof"]["legislation_relationship_rows"], 0
            )

            broken = json.loads(payload_path.read_text(encoding="utf-8"))
            broken["model_sheets"][next(iter(model_sheets))].append(
                ["unexpected"]
            )
            write_json(payload_path, broken)
            with self.assertRaisesRegex(
                EL.LayoutExtractionError, "zero-legislation fixture"
            ):
                EL.resolve_zero_legislation_calibration(
                    capability, baseline=baseline
                )

    def test_sources_enforce_artifact_tool_read_only_contract(self) -> None:
        python_source = (
            FEC / "legislation" / "extract_legislation_workbook_audited_layout.py"
        ).read_text(encoding="utf-8")
        helper_source = (
            FEC
            / "extract_legislation_workbook_audited_layout_artifact.cjs"
        ).read_text(encoding="utf-8")
        forbidden = ("openpyxl", "pandas", "xlsxwriter")
        for token in forbidden:
            self.assertNotIn(token, python_source.casefold())
            self.assertNotIn(token, helper_source.casefold())
        self.assertIn('require("@oai/artifact-tool")', helper_source)
        self.assertIn("SpreadsheetFile.importXlsx", helper_source)
        self.assertNotIn("SpreadsheetFile.exportXlsx", helper_source)
        self.assertNotIn("symlink", python_source.casefold())
        self.assertNotIn("symlink", helper_source.casefold())


if __name__ == "__main__":
    unittest.main()
