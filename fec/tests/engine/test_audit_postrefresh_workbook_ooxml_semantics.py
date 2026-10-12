from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

import audit.audit_postrefresh_workbook_ooxml_semantics as AUDIT  # noqa: E402
from legislation import extract_legislation_workbook_audited_layout as LAYOUT  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def receipt(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def synthetic_xlsx(
    path: Path,
    *,
    input_value: str,
    formula: str,
    formula_cache: str,
    formula_cache_type: str = "n",
    dimension: str = "A1:B2",
    defined_name: str = "Sheet1!$A$1",
    external_target: str = "https://example.test/old",
    second_style_fill: str = "FFFF0000",
    input_style: int = 0,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types"><Default Extension="rels" ContentType="application/'
        'vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        "</Types>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships"><sheets><sheet name="Sheet1" sheetId="1" '
        'r:id="rId1"/></sheets><definedNames><definedName name="Demo">'
        f"{defined_name}</definedName></definedNames><calcPr calcId=\"1\"/>"
        "</workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/externalLink" TargetMode="External" '
        f'Target="{external_target}"/></Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><fonts count="1"><font><name val="Arial"/>'
        '</font></fonts><fills count="2"><fill><patternFill patternType="none"/>'
        '</fill><fill><patternFill patternType="solid"><fgColor rgb="'
        f'{second_style_fill}"/></patternFill></fill></fills>'
        '<borders count="1"><border/></borders><cellStyleXfs count="1">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" '
        'borderId="0" xfId="0"/><xf numFmtId="0" fontId="0" fillId="1" '
        'borderId="0" xfId="0" applyFill="1"/></cellXfs></styleSheet>'
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        f'spreadsheetml/2006/main"><dimension ref="{dimension}"/><sheetData>'
        f'<row r="1"><c r="A1" s="{input_style}"><v>{input_value}</v></c>'
        '</row><row r="2"><c r="B2"'
        + (f' t="{formula_cache_type}"' if formula_cache_type != "n" else "")
        + f"><f>{formula}</f><v>{formula_cache}</v></c></row>"
        "</sheetData></worksheet>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", root_rels)
        package.writestr("xl/workbook.xml", workbook)
        package.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        package.writestr("xl/styles.xml", styles)
        package.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def bound_receipts(
    root: Path,
    *,
    baseline_sha: str,
) -> tuple[Path, Path, Path]:
    layout = write_json(
        root / "layout.json",
        {
            "schema": LAYOUT.SCHEMA,
            "status": "PASS",
            "production_written": False,
            "source_workbook_sha256": baseline_sha,
            "sublayer_formula_audit": {
                "checked_cells": 9594,
                "mismatch_count": 0,
            },
            "press_normalized_formula_audit": {
                "checked_cells": 10660,
                "mismatch_count": 0,
            },
        },
    )
    model = write_json(
        root / "model.json",
        {
            "schema": "legislation-spreadsheet-model-verification-v1",
            "status": "BLOCKED",
            "production_written": False,
            "workbook_edited": False,
            "input": {
                "sha256": baseline_sha,
                "unchanged_after_verification": True,
            },
            "critical_numeric_sample": {"passed": False},
        },
    )
    rank_manifest = write_json(
        root / "rank" / "manifest.json",
        {
            "schema": "post-weekly-refresh-rank-drift-manifest-v1",
            "audit_id": "f" * 64,
            "status": "PASS_OBSERVATIONAL_NONAUTHORITATIVE",
            "authoritative_for_legislation_ranks": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "sync_or_promotion_performed": False,
            "summary": {"member_count": 533},
        },
    )
    rank_pointer = write_json(
        root / "rank" / "current.json",
        {
            "schema": "post-weekly-refresh-rank-drift-pointer-v1",
            "audit_id": "f" * 64,
            "manifest": receipt(rank_manifest),
            "authoritative_for_legislation_ranks": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "sync_or_promotion_performed": False,
        },
    )
    return layout, model, rank_pointer


class WorkbookOoxmlSemanticAuditTests(unittest.TestCase):
    def test_compares_every_supported_semantic_surface(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline = synthetic_xlsx(
                root / "baseline.xlsx",
                input_value="1",
                formula="SUM(A1:A1)",
                formula_cache="1",
            )
            live = synthetic_xlsx(
                root / "live.xlsx",
                input_value="2",
                input_style=1,
                formula="SUM(A1:A2)",
                formula_cache="#VALUE!",
                formula_cache_type="e",
                dimension="A1:C3",
                defined_name="Sheet1!$A$2",
                external_target="https://example.test/new",
                second_style_fill="FF00FF00",
            )
            comparison = AUDIT.compare_workbooks(baseline, live)
        summary = comparison["summary"]
        self.assertTrue(summary["sheet_order_and_names_identical"])
        self.assertEqual(summary["sheet_dimension_mismatch_count"], 1)
        self.assertEqual(summary["formula_mismatch_count"], 1)
        self.assertEqual(
            summary["formula_cached_value_mismatch_count"], 1
        )
        self.assertEqual(summary["live_formula_cached_error_count"], 1)
        self.assertGreaterEqual(summary["changed_cell_count"], 2)
        self.assertGreater(summary["defined_name_mismatch_count"], 0)
        self.assertGreater(summary["relationship_mismatch_count"], 0)
        self.assertGreater(summary["style_definition_mismatch_count"], 0)
        by_cell = {
            (row["sheet"], row["address"]): row
            for row in comparison["cell_rows"]
        }
        self.assertIn("formula_text", by_cell[("Sheet1", "B2")]["changed_fields"])
        self.assertIn(
            "formula_cached_value",
            by_cell[("Sheet1", "B2")]["changed_fields"],
        )
        self.assertIn(
            "effective_style_sha256",
            by_cell[("Sheet1", "A1")]["changed_fields"],
        )

    def test_content_addressed_idempotence_and_tamper_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            production = synthetic_xlsx(
                root / LAYOUT.WORKBOOK_BASENAME,
                input_value="2",
                formula="SUM(A1:A1)",
                formula_cache="2",
            )
            frozen = synthetic_xlsx(
                root / "baseline" / "files" / LAYOUT.WORKBOOK_BASENAME,
                input_value="1",
                formula="SUM(A1:A1)",
                formula_cache="1",
            )
            manifest = write_json(
                root / "baseline" / "baseline_manifest.json",
                {
                    "schema": LAYOUT.BASELINE_SCHEMA,
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
            layout, model, rank_pointer = bound_receipts(
                root, baseline_sha=sha256(frozen)
            )
            out = root / "out"
            baseline_before = sha256(frozen)
            live_before = sha256(production)
            snapshot, first, created = AUDIT.build_audit(
                baseline_manifest_path=manifest,
                layout_receipt_path=layout,
                model_receipt_path=model,
                rank_pointer_path=rank_pointer,
                out_root=out,
                expected_baseline_sha256=baseline_before,
                expected_live_sha256=live_before,
            )
            self.assertTrue(created)
            pointer = out / "current.json"
            pointer_bytes = pointer.read_bytes()
            pointer_mtime = pointer.stat().st_mtime_ns
            time.sleep(0.01)
            same_snapshot, second, created = AUDIT.build_audit(
                baseline_manifest_path=manifest,
                layout_receipt_path=layout,
                model_receipt_path=model,
                rank_pointer_path=rank_pointer,
                out_root=out,
                expected_baseline_sha256=baseline_before,
                expected_live_sha256=live_before,
            )
            self.assertFalse(created)
            self.assertEqual(snapshot, same_snapshot)
            self.assertEqual(first["audit_id"], second["audit_id"])
            self.assertEqual(pointer.read_bytes(), pointer_bytes)
            self.assertEqual(pointer.stat().st_mtime_ns, pointer_mtime)
            self.assertEqual(sha256(frozen), baseline_before)
            self.assertEqual(sha256(production), live_before)

            cell_ledger = snapshot / "cell_differences.ndjson"
            cell_ledger.write_bytes(cell_ledger.read_bytes() + b"tamper\n")
            with self.assertRaisesRegex(
                AUDIT.WorkbookSemanticAuditError, "receipt drift"
            ):
                AUDIT.build_audit(
                    baseline_manifest_path=manifest,
                    layout_receipt_path=layout,
                    model_receipt_path=model,
                    rank_pointer_path=rank_pointer,
                    out_root=out,
                    expected_baseline_sha256=baseline_before,
                    expected_live_sha256=live_before,
                )


if __name__ == "__main__":
    unittest.main()
