from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

import audit.audit_formula_cache_freshness as AUDIT  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def synthetic_xlsx(
    path: Path,
    *,
    input_value: str,
    cache_value: str,
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
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships"><sheets><sheet name="Sheet1" sheetId="1" '
        'r:id="rId1"/></sheets><calcPr calcId="1" calcMode="manual"/>'
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
        'relationships/styles" Target="styles.xml"/></Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><fonts count="1"><font/></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill>'
        '</fills><borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
        'borderId="0"/></cellStyleXfs><cellXfs count="1"><xf numFmtId="0" '
        'fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        "</styleSheet>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><dimension ref="A1:B1"/><sheetData>'
        f'<row r="1"><c r="A1"><v>{input_value}</v></c>'
        f'<c r="B1"><f>A1</f><v>{cache_value}</v></c></row>'
        "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", root_rels)
        package.writestr("xl/workbook.xml", workbook)
        package.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        package.writestr("xl/styles.xml", styles)
        package.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def engine_receipt(
    path: Path,
    *,
    workbook: Path,
    source_sha256: str,
) -> Path:
    body = {
        "schema": AUDIT.ENGINE_SCHEMA,
        "status": "PASS_TEMP_COPY_RECALCULATED",
        "source_expected_sha256": source_sha256,
        "temporary_copy_before": {
            "path": str(workbook.resolve()),
            "bytes": 1,
            "sha256": source_sha256,
        },
        "temporary_copy_after": {
            "path": str(workbook.resolve()),
            "bytes": workbook.stat().st_size,
            "sha256": sha256(workbook),
        },
        "safety_preflight": {
            "safe_for_isolated_excel_calculation": True,
            "forbidden_package_parts": [],
            "unsafe_external_relationships": [],
            "unsafe_formula_examples": [],
            "external_formula_reference_examples": [],
            "defined_name_runtime_risks": [],
        },
        "engine": {
            "automation_security": "msoAutomationSecurityForceDisable",
            "update_links": 0,
            "enable_events": False,
            "display_alerts": False,
            "ask_to_update_links": False,
            "visible": False,
            "calculation_request": "CalculateFull",
            "calculation_state_after": 0,
            "ready_after": True,
            "calculate_before_save": False,
        },
        "live_or_production_workbook_opened": False,
        "live_or_production_workbook_written": False,
        "macro_execution_allowed": False,
        "external_link_updates_allowed": False,
        "external_connections_allowed": False,
        "sync_or_promotion_performed": False,
    }
    path.write_text(
        json.dumps(body, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


class FormulaCacheFreshnessAuditTests(unittest.TestCase):
    def test_inventory_distinguishes_blank_and_materialized_cache(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            blank = synthetic_xlsx(
                root / "blank.xlsx", input_value="2", cache_value=""
            )
            calculated = synthetic_xlsx(
                root / "calculated.xlsx", input_value="2", cache_value="2"
            )
            blank_inventory = AUDIT.formula_cache_inventory(blank)
            calculated_inventory = AUDIT.formula_cache_inventory(calculated)
        self.assertEqual(blank_inventory["formula_count"], 1)
        self.assertEqual(blank_inventory["blank_cache_count"], 1)
        self.assertEqual(calculated_inventory["nonblank_cache_count"], 1)
        self.assertNotEqual(
            blank_inventory["formula_cache_sha256"],
            calculated_inventory["formula_cache_sha256"],
        )

    def test_two_independent_copies_pass_without_claiming_live_freshness(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            temp_root = root / "calc"
            live = synthetic_xlsx(
                root / "live" / "live.xlsx",
                input_value="2",
                cache_value="",
            )
            calculated_a = synthetic_xlsx(
                temp_root / "a.xlsx", input_value="2", cache_value="2"
            )
            calculated_b = synthetic_xlsx(
                temp_root / "b.xlsx", input_value="2", cache_value="2"
            )
            source_sha256 = sha256(live)
            receipt_a = engine_receipt(
                temp_root / "a.json",
                workbook=calculated_a,
                source_sha256=source_sha256,
            )
            receipt_b = engine_receipt(
                temp_root / "b.json",
                workbook=calculated_b,
                source_sha256=source_sha256,
            )
            snapshot, manifest, created = AUDIT.build_audit(
                source_path=live,
                calculated_a_path=calculated_a,
                calculated_b_path=calculated_b,
                receipt_a_path=receipt_a,
                receipt_b_path=receipt_b,
                out_root=root / "audit",
                expected_source_sha256=source_sha256,
                temp_root=temp_root,
            )
            self.assertTrue(created)
            self.assertTrue(snapshot.is_dir())
            self.assertTrue(
                manifest["gates"]["temporary_recalculation_passed"]
            )
            self.assertFalse(
                manifest["gates"][
                    "live_stored_formula_cache_freshness_proven"
                ]
            )
            self.assertFalse(manifest["sync_or_promotion_performed"])
            _, second, created = AUDIT.build_audit(
                source_path=live,
                calculated_a_path=calculated_a,
                calculated_b_path=calculated_b,
                receipt_a_path=receipt_a,
                receipt_b_path=receipt_b,
                out_root=root / "audit",
                expected_source_sha256=source_sha256,
                temp_root=temp_root,
            )
            self.assertFalse(created)
            self.assertEqual(manifest["audit_id"], second["audit_id"])

    def test_divergent_independent_caches_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            temp_root = root / "calc"
            live = synthetic_xlsx(
                root / "live" / "live.xlsx",
                input_value="2",
                cache_value="",
            )
            calculated_a = synthetic_xlsx(
                temp_root / "a.xlsx", input_value="2", cache_value="2"
            )
            calculated_b = synthetic_xlsx(
                temp_root / "b.xlsx", input_value="2", cache_value="3"
            )
            source_sha256 = sha256(live)
            receipt_a = engine_receipt(
                temp_root / "a.json",
                workbook=calculated_a,
                source_sha256=source_sha256,
            )
            receipt_b = engine_receipt(
                temp_root / "b.json",
                workbook=calculated_b,
                source_sha256=source_sha256,
            )
            with self.assertRaisesRegex(
                AUDIT.FormulaCacheAuditError,
                "deterministic or static-semantics",
            ):
                AUDIT.build_audit(
                    source_path=live,
                    calculated_a_path=calculated_a,
                    calculated_b_path=calculated_b,
                    receipt_a_path=receipt_a,
                    receipt_b_path=receipt_b,
                    out_root=root / "audit",
                    expected_source_sha256=source_sha256,
                    temp_root=temp_root,
                )

    def test_nonformula_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            temp_root = root / "calc"
            live = synthetic_xlsx(
                root / "live" / "live.xlsx",
                input_value="2",
                cache_value="",
            )
            calculated_a = synthetic_xlsx(
                temp_root / "a.xlsx", input_value="9", cache_value="9"
            )
            calculated_b = synthetic_xlsx(
                temp_root / "b.xlsx", input_value="9", cache_value="9"
            )
            source_sha256 = sha256(live)
            receipt_a = engine_receipt(
                temp_root / "a.json",
                workbook=calculated_a,
                source_sha256=source_sha256,
            )
            receipt_b = engine_receipt(
                temp_root / "b.json",
                workbook=calculated_b,
                source_sha256=source_sha256,
            )
            with self.assertRaisesRegex(
                AUDIT.FormulaCacheAuditError,
                "deterministic or static-semantics",
            ):
                AUDIT.build_audit(
                    source_path=live,
                    calculated_a_path=calculated_a,
                    calculated_b_path=calculated_b,
                    receipt_a_path=receipt_a,
                    receipt_b_path=receipt_b,
                    out_root=root / "audit",
                    expected_source_sha256=source_sha256,
                    temp_root=temp_root,
                )


if __name__ == "__main__":
    unittest.main()
