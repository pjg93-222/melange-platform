#!/usr/bin/env python3
"""Audit deterministic formula caches produced on isolated workbook copies.

The live workbook is always read-only.  Calculation is delegated to
``recalculate_formula_cache_copy.ps1``, which refuses non-``C:\\tmp`` inputs
and disables macros, events, connections, and external link updates.  This
producer compares two independently recalculated copies, verifies that only
formula-cache representations changed, and emits a content-addressed audit
receipt.  It never syncs or promotes a workbook.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any, Mapping
import uuid
import xml.etree.ElementTree as ET
import zipfile

import audit.audit_postrefresh_workbook_ooxml_semantics as OOXML


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "Moneyball Main Version - Blended.xlsx"
DEFAULT_OUT_ROOT = (
    ROOT / "audits" / "legfix5" / "formula_cache_freshness"
)
SCHEMA = "isolated-formula-cache-freshness-audit-v1"
POINTER_SCHEMA = "isolated-formula-cache-freshness-pointer-v1"
ENGINE_SCHEMA = "isolated-excel-formula-cache-recalculation-v1"
# D-174: LibreOffice headless as the calculator (ops/compute/office_recalc.py). Opt-in; Excel receipts validate as before.
LIBREOFFICE_ENGINE_SCHEMA = "isolated-libreoffice-formula-cache-recalculation-v1"
ERROR_RE = re.compile(
    r"#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A|GETTING_DATA|SPILL!|CALC!)",
    re.IGNORECASE,
)
MAIN_NS = OOXML.MAIN_NS


class FormulaCacheAuditError(RuntimeError):
    """A safety, provenance, or deterministic-cache gate failed closed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise FormulaCacheAuditError(message)


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_item(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    require(resolved.is_file(), f"Missing bound file: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def read_json(path: Path) -> Any:
    require(path.is_file(), f"Missing JSON input: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise FormulaCacheAuditError(f"Unreadable JSON input: {path}") from exc


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def validate_engine_receipt(
    *,
    receipt_path: Path,
    workbook_path: Path,
    expected_source_sha256: str,
    temp_root: Path,
) -> dict[str, Any]:
    receipt = read_json(receipt_path)
    if (
        isinstance(receipt, dict)
        and receipt.get("schema") == LIBREOFFICE_ENGINE_SCHEMA
    ):
        return validate_libreoffice_receipt(
            receipt=receipt,
            receipt_path=receipt_path,
            workbook_path=workbook_path,
            expected_source_sha256=expected_source_sha256,
            temp_root=temp_root,
        )
    require(
        isinstance(receipt, dict)
        and receipt.get("schema") == ENGINE_SCHEMA
        and receipt.get("status") == "PASS_TEMP_COPY_RECALCULATED",
        f"Engine receipt failed or has the wrong schema: {receipt_path}",
    )
    workbook_path = workbook_path.resolve()
    require(
        _is_within(workbook_path, temp_root),
        f"Calculated workbook escaped the verified temp root: {workbook_path}",
    )
    before = receipt.get("temporary_copy_before") or {}
    after = receipt.get("temporary_copy_after") or {}
    engine = receipt.get("engine") or {}
    safety = receipt.get("safety_preflight") or {}
    require(
        Path(str(before.get("path") or "")).resolve() == workbook_path
        and Path(str(after.get("path") or "")).resolve() == workbook_path,
        "Engine receipt path does not bind the calculated workbook",
    )
    require(
        receipt.get("source_expected_sha256") == expected_source_sha256
        and before.get("sha256") == expected_source_sha256,
        "Engine receipt does not bind the source workbook hash",
    )
    calculated_item = file_item(workbook_path)
    require(
        after.get("sha256") == calculated_item["sha256"]
        and after.get("bytes") == calculated_item["bytes"],
        "Calculated workbook drifted after the engine receipt",
    )
    require(
        safety.get("safe_for_isolated_excel_calculation") is True
        and not safety.get("forbidden_package_parts")
        and not safety.get("unsafe_external_relationships")
        and not safety.get("unsafe_formula_examples")
        and not safety.get("external_formula_reference_examples")
        and not safety.get("defined_name_runtime_risks"),
        "Engine safety preflight is incomplete or failed",
    )
    require(
        engine.get("automation_security")
        == "msoAutomationSecurityForceDisable"
        and engine.get("update_links") == 0
        and engine.get("enable_events") is False
        and engine.get("display_alerts") is False
        and engine.get("ask_to_update_links") is False
        and engine.get("visible") is False
        and engine.get("calculation_request")
        in {"CalculateFull", "CalculateFullRebuild"}
        and engine.get("calculation_state_after") == 0
        and engine.get("ready_after") is True
        and engine.get("calculate_before_save") is False,
        "Engine isolation or completed-calculation evidence is missing",
    )
    require(
        receipt.get("live_or_production_workbook_opened") is False
        and receipt.get("live_or_production_workbook_written") is False
        and receipt.get("macro_execution_allowed") is False
        and receipt.get("external_link_updates_allowed") is False
        and receipt.get("external_connections_allowed") is False
        and receipt.get("sync_or_promotion_performed") is False,
        "Engine receipt violates the no-live-write safety contract",
    )
    return {
        "receipt": file_item(receipt_path),
        "workbook": calculated_item,
        "engine": engine,
        "safety_preflight": safety,
    }


def _libreoffice_at_least(version: Any, minimum: tuple[int, int]) -> bool:
    match = re.match(r"(\d+)\.(\d+)", str(version or ""))
    return bool(match) and (int(match.group(1)), int(match.group(2))) >= minimum


def validate_libreoffice_receipt(
    *,
    receipt: dict[str, Any],
    receipt_path: Path,
    workbook_path: Path,
    expected_source_sha256: str,
    temp_root: Path,
) -> dict[str, Any]:
    """The LibreOffice counterpart of the Excel checks above (D-174).

    The calculator never saves the workbook it loads: it writes a view copy (original package plus injected cached
    values) elsewhere, usually inside a container. So the copy is bound by hash and size, not by path, and the input
    copy and the view copy are two different files.
    """
    require(
        receipt.get("status") == "PASS_TEMP_COPY_RECALCULATED",
        f"Engine receipt failed or has the wrong status: {receipt_path}",
    )
    workbook_path = workbook_path.resolve()
    require(
        _is_within(workbook_path, temp_root),
        f"Calculated workbook escaped the verified temp root: {workbook_path}",
    )
    before = receipt.get("temporary_copy_before") or {}
    after = receipt.get("temporary_copy_after") or {}
    engine = receipt.get("engine") or {}
    safety = receipt.get("safety_preflight") or {}
    require(
        receipt.get("source_expected_sha256") == expected_source_sha256
        and before.get("sha256") == expected_source_sha256,
        "Engine receipt does not bind the source workbook hash",
    )
    calculated_item = file_item(workbook_path)
    require(
        after.get("sha256") == calculated_item["sha256"]
        and after.get("bytes") == calculated_item["bytes"],
        "Calculated workbook drifted after the engine receipt",
    )
    require(
        receipt.get("view_copy") is True
        and receipt.get("canonical_workbook_written") is False
        and receipt.get("input_unchanged") is True,
        "Engine receipt does not describe an untouched input and a view copy",
    )
    require(
        safety.get("safe_for_isolated_calculation") is True
        and not safety.get("forbidden_package_parts")
        and not safety.get("unsafe_external_relationships")
        and not safety.get("unsafe_formula_examples")
        and not safety.get("external_formula_reference_examples")
        and not safety.get("defined_name_runtime_risks")
        and safety.get("array_formula_count") == 0,
        "Engine safety preflight is incomplete or failed",
    )
    require(
        engine.get("product") == "LibreOffice"
        and _libreoffice_at_least(engine.get("version"), (24, 8))
        and engine.get("automation_security")
        == "MacroExecutionMode.NEVER_EXECUTE"
        and engine.get("update_links") == 0
        and engine.get("enable_events") is False
        and engine.get("display_alerts") is False
        and engine.get("ask_to_update_links") is False
        and engine.get("visible") is False
        and engine.get("read_only") is True
        and engine.get("calculation_request") == "calculateAll"
        and engine.get("calculation_completed") is True
        and engine.get("calculate_before_save") is False
        and engine.get("document_stored") is False,
        "Engine isolation or completed-calculation evidence is missing",
    )
    require(
        receipt.get("live_or_production_workbook_opened") is False
        and receipt.get("live_or_production_workbook_written") is False
        and receipt.get("macro_execution_allowed") is False
        and receipt.get("external_link_updates_allowed") is False
        and receipt.get("external_connections_allowed") is False
        and receipt.get("sync_or_promotion_performed") is False,
        "Engine receipt violates the no-live-write safety contract",
    )
    return {
        "receipt": file_item(receipt_path),
        "workbook": calculated_item,
        "engine": engine,
        "safety_preflight": safety,
    }


def formula_cache_inventory(path: Path) -> dict[str, Any]:
    before = file_item(path)
    cache_rows: list[dict[str, Any]] = []
    formula_rows: list[dict[str, Any]] = []
    error_counts: Counter[str] = Counter()
    blank_count = 0
    missing_count = 0
    try:
        package_context = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise FormulaCacheAuditError(f"Invalid XLSX package: {path}") from exc
    with package_context as package:
        model = OOXML.workbook_model(package)
        for sheet in model["sheets"]:
            target = str(sheet["target"])
            require(target in package.namelist(), f"Missing sheet part: {target}")
            try:
                root = ET.fromstring(package.read(target))
            except ET.ParseError as exc:
                raise FormulaCacheAuditError(
                    f"Invalid worksheet XML: {sheet['name']}"
                ) from exc
            for cell in root.findall(
                f".//{{{MAIN_NS}}}sheetData/"
                f"{{{MAIN_NS}}}row/{{{MAIN_NS}}}c"
            ):
                formula = cell.find(f"{{{MAIN_NS}}}f")
                if formula is None:
                    continue
                address = str(cell.get("r") or "").upper()
                formula_rows.append(
                    {
                        "sheet": sheet["name"],
                        "address": address,
                        "text": formula.text or "",
                        "attributes": sorted(
                            (str(key), str(value))
                            for key, value in formula.attrib.items()
                        ),
                    }
                )
                cached = cell.find(f"{{{MAIN_NS}}}v")
                present = cached is not None
                text = cached.text if cached is not None else None
                if not present:
                    missing_count += 1
                elif text is None or not text.strip():
                    blank_count += 1
                token = {
                    "sheet": sheet["name"],
                    "address": address,
                    "cell_type": str(cell.get("t") or "n"),
                    "present": present,
                    "text": text,
                }
                cache_rows.append(token)
                if present and text:
                    match = ERROR_RE.search(text)
                    if str(cell.get("t") or "") == "e" or match:
                        error_counts[
                            match.group(0).upper() if match else text
                        ] += 1
    after = file_item(path)
    require(before == after, "Workbook changed during cache inventory")
    return {
        "workbook": before,
        "formula_count": len(formula_rows),
        "formula_definition_sha256": sha256_bytes(
            canonical_bytes(formula_rows)
        ),
        "formula_cache_sha256": sha256_bytes(canonical_bytes(cache_rows)),
        "cache_element_present_count": len(cache_rows) - missing_count,
        "cache_element_missing_count": missing_count,
        "blank_cache_count": blank_count,
        "nonblank_cache_count": len(cache_rows) - missing_count - blank_count,
        "formula_cached_error_count": sum(error_counts.values()),
        "formula_cached_errors_by_token": dict(sorted(error_counts.items())),
    }


def semantic_comparison(
    source: Path,
    calculated: Path,
) -> dict[str, Any]:
    comparison = OOXML.compare_workbooks(source, calculated)
    summary = comparison["summary"]
    unsafe_cell_rows = []
    logical_nonformula_mismatches = []

    def logical_value(value: Mapping[str, Any] | None) -> tuple[str, Any]:
        if not value or value.get("present") is not True:
            return ("blank", None)
        value_type = str(value.get("value_type") or "")
        item = value.get("value")
        if item is None or value_type == "blank_numeric_cache_or_value":
            return ("blank", None)
        if value_type == "number":
            try:
                return ("number", float(str(item)))
            except ValueError:
                return ("invalid_number", str(item))
        if value_type in {
            "shared_string",
            "inline_string",
            "string",
        }:
            return ("string", str(item))
        if value_type == "boolean":
            return ("boolean", bool(item))
        if value_type == "error":
            return ("error", str(item))
        return (value_type, item)

    for row in comparison["cell_rows"]:
        old = row.get("baseline") or {}
        new = row.get("live") or {}
        both_formula = old.get("formula") is not None and new.get("formula") is not None
        fields = set(row.get("changed_fields") or [])
        if not both_formula or not fields.issubset(
            {"cached_value", "formula_cached_value", "cell_type"}
        ):
            unsafe_cell_rows.append(
                {
                    "sheet": row.get("sheet"),
                    "address": row.get("address"),
                    "changed_fields": sorted(fields),
                }
            )
        if old.get("formula") is None and new.get("formula") is None:
            old_logical = logical_value(old.get("value"))
            new_logical = logical_value(new.get("value"))
            if old_logical != new_logical:
                logical_nonformula_mismatches.append(
                    {
                        "sheet": row.get("sheet"),
                        "address": row.get("address"),
                        "baseline": old_logical,
                        "calculated": new_logical,
                    }
                )
    unsafe_sheet_rows = [
        {
            "sheet": row["sheet"],
            "metadata_identical": row["metadata_identical"],
            "dimension_identical": row["dimension_identical"],
            "structure_without_cells_identical": row[
                "structure_without_cells_identical"
            ],
        }
        for row in comparison["sheet_rows"]
        if not (
            row["change"] == "present_in_both"
            and row["metadata_identical"]
            and row["dimension_identical"]
            and row["structure_without_cells_identical"]
        )
    ]
    static_semantics_preserved = (
        not unsafe_cell_rows
        and not unsafe_sheet_rows
        and summary["sheet_order_and_names_identical"]
        and summary["formula_mismatch_count"] == 0
        and summary["defined_name_mismatch_count"] == 0
        and summary["relationship_mismatch_count"] == 0
        and summary["style_definition_mismatch_count"] == 0
        and summary["effective_style_mismatch_count"] == 0
        and summary["cell_added_count"] == 0
        and summary["cell_removed_count"] == 0
        and summary["nonformula_value_mismatch_count"] == 0
    )
    return {
        "static_semantics_preserved": bool(static_semantics_preserved),
        "unsafe_cell_change_count": len(unsafe_cell_rows),
        "unsafe_cell_change_examples": unsafe_cell_rows[:25],
        "logical_nonformula_mismatch_count": len(
            logical_nonformula_mismatches
        ),
        "logical_nonformula_mismatch_examples": (
            logical_nonformula_mismatches[:25]
        ),
        "unsafe_sheet_change_count": len(unsafe_sheet_rows),
        "unsafe_sheet_change_examples": unsafe_sheet_rows[:25],
        "formula_cache_changed_cell_count": summary[
            "formula_cached_value_mismatch_count"
        ],
        "formula_cache_only_changed_cell_count": summary[
            "formula_cached_only_changed_cell_count"
        ],
        "cell_type_mismatch_count": summary["cell_type_mismatch_count"],
        "formula_mismatch_count": summary["formula_mismatch_count"],
        "nonformula_value_mismatch_count": summary[
            "nonformula_value_mismatch_count"
        ],
        "relationship_mismatch_count": summary[
            "relationship_mismatch_count"
        ],
        "defined_name_mismatch_count": summary[
            "defined_name_mismatch_count"
        ],
        "style_definition_mismatch_count": summary[
            "style_definition_mismatch_count"
        ],
        "effective_style_mismatch_count": summary[
            "effective_style_mismatch_count"
        ],
        "package_part_added_count": summary["package_part_added_count"],
        "package_part_removed_count": summary["package_part_removed_count"],
    }


def _write_if_changed(path: Path, body: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == body:
        return False
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(body)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def _cleanup_temporary(path: Path, expected_parent: Path) -> None:
    path = path.resolve()
    expected_parent = expected_parent.resolve()
    require(
        path.parent == expected_parent
        and path.name.startswith(".formula-cache-freshness."),
        f"Unsafe temporary cleanup target: {path}",
    )

    def onexc(function: Any, target: str, error: BaseException) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=onexc)


def build_audit(
    *,
    source_path: Path,
    calculated_a_path: Path,
    calculated_b_path: Path,
    receipt_a_path: Path,
    receipt_b_path: Path,
    out_root: Path,
    expected_source_sha256: str,
    temp_root: Path = Path("C:/tmp"),
) -> tuple[Path, dict[str, Any], bool]:
    source_path = source_path.resolve()
    calculated_a_path = calculated_a_path.resolve()
    calculated_b_path = calculated_b_path.resolve()
    receipt_a_path = receipt_a_path.resolve()
    receipt_b_path = receipt_b_path.resolve()
    out_root = out_root.resolve()
    temp_root = temp_root.resolve()
    source_before = file_item(source_path)
    require(
        source_before["sha256"] == expected_source_sha256,
        "Live/source workbook hash drifted before formula-cache audit",
    )
    require(
        not _is_within(source_path, temp_root),
        "The live/source workbook must not be the temp calculation target",
    )
    engine_a = validate_engine_receipt(
        receipt_path=receipt_a_path,
        workbook_path=calculated_a_path,
        expected_source_sha256=expected_source_sha256,
        temp_root=temp_root,
    )
    engine_b = validate_engine_receipt(
        receipt_path=receipt_b_path,
        workbook_path=calculated_b_path,
        expected_source_sha256=expected_source_sha256,
        temp_root=temp_root,
    )
    source_inventory = formula_cache_inventory(source_path)
    inventory_a = formula_cache_inventory(calculated_a_path)
    inventory_b = formula_cache_inventory(calculated_b_path)
    source_vs_a = semantic_comparison(source_path, calculated_a_path)
    source_vs_b = semantic_comparison(source_path, calculated_b_path)
    a_vs_b = semantic_comparison(calculated_a_path, calculated_b_path)
    source_after = file_item(source_path)
    require(
        source_before == source_after,
        "Live/source workbook changed during formula-cache audit",
    )

    formula_count_parity = (
        source_inventory["formula_count"]
        == inventory_a["formula_count"]
        == inventory_b["formula_count"]
    )
    source_to_postsave_exact_formula_encoding_parity = (
        source_inventory["formula_definition_sha256"]
        == inventory_a["formula_definition_sha256"]
        == inventory_b["formula_definition_sha256"]
    )
    independent_postsave_formula_definition_parity = (
        inventory_a["formula_definition_sha256"]
        == inventory_b["formula_definition_sha256"]
    )
    deterministic_cache_parity = (
        inventory_a["formula_cache_sha256"]
        == inventory_b["formula_cache_sha256"]
    )
    calculation_materialized = (
        inventory_a["formula_cache_sha256"]
        != source_inventory["formula_cache_sha256"]
        and inventory_a["nonblank_cache_count"] > 0
        and source_vs_a["formula_cache_changed_cell_count"] > 0
        and source_vs_b["formula_cache_changed_cell_count"] > 0
    )
    no_formula_errors = (
        inventory_a["formula_cached_error_count"] == 0
        and inventory_b["formula_cached_error_count"] == 0
    )
    temp_recalculation_passed = (
        formula_count_parity
        and independent_postsave_formula_definition_parity
        and deterministic_cache_parity
        and calculation_materialized
        and no_formula_errors
        and source_vs_a["logical_nonformula_mismatch_count"] == 0
        and source_vs_b["logical_nonformula_mismatch_count"] == 0
        and a_vs_b["static_semantics_preserved"]
    )
    postsave_candidate_exact_semantics_preserved = (
        source_to_postsave_exact_formula_encoding_parity
        and source_vs_a["static_semantics_preserved"]
        and source_vs_b["static_semantics_preserved"]
    )
    failure_summary = {
        "formula_count_parity": formula_count_parity,
        "source_to_postsave_exact_formula_encoding_parity": (
            source_to_postsave_exact_formula_encoding_parity
        ),
        "independent_postsave_formula_definition_parity": (
            independent_postsave_formula_definition_parity
        ),
        "deterministic_cache_parity": deterministic_cache_parity,
        "calculation_materialized": calculation_materialized,
        "no_formula_errors": no_formula_errors,
        "source_vs_a": source_vs_a,
        "source_vs_b": source_vs_b,
        "a_vs_b": a_vs_b,
        "source_inventory": source_inventory,
        "inventory_a": inventory_a,
        "inventory_b": inventory_b,
    }
    require(
        temp_recalculation_passed,
        "Independent temp-copy formula recalculations failed deterministic or "
        "static-semantics gates: "
        + json.dumps(failure_summary, sort_keys=True),
    )

    content_id = sha256_bytes(
        canonical_bytes(
            {
                "schema": SCHEMA,
                "source_sha256": expected_source_sha256,
                "calculated_a_sha256": inventory_a["workbook"]["sha256"],
                "calculated_b_sha256": inventory_b["workbook"]["sha256"],
                "cache_sha256": inventory_a["formula_cache_sha256"],
                "engine_a_receipt_sha256": engine_a["receipt"]["sha256"],
                "engine_b_receipt_sha256": engine_b["receipt"]["sha256"],
            }
        )
    )
    snapshot = out_root / "snapshots" / content_id
    temporary = out_root / f".formula-cache-freshness.{uuid.uuid4().hex}"
    manifest_path = snapshot / "manifest.json"
    created = False
    try:
        temporary.mkdir(parents=True, exist_ok=False)
        generated_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "schema": SCHEMA,
            "audit_id": content_id,
            "status": (
                "PASS_TEMP_CACHE_RECOMPUTATION_"
                "LIVE_AND_PROMOTION_GATES_UNCHANGED"
            ),
            "generated_at": generated_at,
            "source_workbook": source_before,
            "source_formula_cache_inventory": source_inventory,
            "independent_calculations": {
                "a": engine_a,
                "b": engine_b,
            },
            "calculated_formula_cache_inventories": {
                "a": inventory_a,
                "b": inventory_b,
            },
            "semantic_comparisons": {
                "source_vs_a": source_vs_a,
                "source_vs_b": source_vs_b,
                "a_vs_b": a_vs_b,
            },
            "gates": {
                "source_hash_unchanged": True,
                "calculation_on_verified_temp_copies_only": True,
                "macros_events_connections_and_link_updates_disabled": True,
                "formula_count_parity": formula_count_parity,
                "current_input_bytes_bound_before_each_calculation": True,
                "independent_postsave_formula_definitions_identical": (
                    independent_postsave_formula_definition_parity
                ),
                "source_to_postsave_exact_formula_encoding_identical": (
                    source_to_postsave_exact_formula_encoding_parity
                ),
                "source_to_postsave_logical_nonformula_values_identical": (
                    source_vs_a["logical_nonformula_mismatch_count"] == 0
                    and source_vs_b["logical_nonformula_mismatch_count"] == 0
                ),
                "independent_postsave_static_semantics_identical": (
                    a_vs_b["static_semantics_preserved"]
                ),
                "independent_formula_caches_identical": deterministic_cache_parity,
                "formula_calculation_materialized": calculation_materialized,
                "calculated_formula_cache_error_count_zero": no_formula_errors,
                "temporary_recalculation_passed": temp_recalculation_passed,
                "postsave_candidate_exact_semantics_preserved": (
                    postsave_candidate_exact_semantics_preserved
                ),
                "postsave_candidate_promotion_eligible": False,
                "live_stored_formula_cache_freshness_proven": False,
                "live_workbook_write_required_to_materialize_caches": True,
            },
            "observational_only": True,
            "authoritative_for_live_stored_formula_caches": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "live_or_production_workbook_written": False,
            "sync_or_promotion_performed": False,
            "unresolved": [
                {
                    "surface": "postsave_candidate_exact_semantics",
                    "reason": (
                        "Excel deterministically rewrote OOXML storage after "
                        "calculation, including shared-formula encoding, "
                        "numeric serialization, style catalogs, relationship "
                        "identifiers, and sheet metadata. The two independent "
                        "post-save copies are semantically identical to each "
                        "other and preserve logical non-formula values, but "
                        "exact source-to-post-save formula/style/structure "
                        "parity is not proven. The calculated copies are not "
                        "promotion eligible."
                    ),
                },
                {
                    "surface": "live_stored_formula_cache_freshness",
                    "reason": (
                        "The current-input formula results were independently "
                        "recalculated and matched on verified temp copies, but "
                        "the live workbook remains byte-identical with its "
                        "pre-existing stored caches. Materializing those caches "
                        "would require a prohibited live-workbook write or an "
                        "explicitly approved promotion of a staged workbook."
                    ),
                }
            ],
        }
        (temporary / "manifest.json").write_bytes(pretty_bytes(manifest))
        if snapshot.exists():
            existing = read_json(manifest_path)
            require(
                existing.get("audit_id") == content_id,
                "Formula-cache snapshot collision",
            )
        else:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, snapshot)
            created = True
        pointer = {
            "schema": POINTER_SCHEMA,
            "audit_id": content_id,
            "status": manifest["status"],
            "manifest": file_item(manifest_path),
            "source_workbook": source_before,
            "temporary_recalculation_passed": True,
            "live_stored_formula_cache_freshness_proven": False,
            "authoritative_for_sync_or_promotion": False,
            "live_or_production_workbook_written": False,
            "sync_or_promotion_performed": False,
        }
        _write_if_changed(out_root / "current.json", pretty_bytes(pointer))
        return snapshot, read_json(manifest_path), created
    finally:
        _cleanup_temporary(temporary, out_root)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    value.add_argument("--calculated-a", type=Path, required=True)
    value.add_argument("--calculated-b", type=Path, required=True)
    value.add_argument("--receipt-a", type=Path, required=True)
    value.add_argument("--receipt-b", type=Path, required=True)
    value.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    value.add_argument("--expected-source-sha256", required=True)
    value.add_argument("--temp-root", type=Path, default=Path("C:/tmp"))
    return value


def main() -> int:
    args = parser().parse_args()
    snapshot, manifest, created = build_audit(
        source_path=args.source,
        calculated_a_path=args.calculated_a,
        calculated_b_path=args.calculated_b,
        receipt_a_path=args.receipt_a,
        receipt_b_path=args.receipt_b,
        out_root=args.out_root,
        expected_source_sha256=args.expected_source_sha256.lower(),
        temp_root=args.temp_root,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "audit_id": manifest["audit_id"],
                "snapshot": str(snapshot),
                "created": created,
                "source_sha256": manifest["source_workbook"]["sha256"],
                "formula_count": manifest[
                    "source_formula_cache_inventory"
                ]["formula_count"],
                "temporary_recalculation_passed": manifest["gates"][
                    "temporary_recalculation_passed"
                ],
                "live_stored_formula_cache_freshness_proven": False,
                "sync_or_promotion_performed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
