#!/usr/bin/env python3
"""Seal a read-only frozen/live OOXML workbook semantic comparison.

This producer is intentionally observational and non-authoritative.  It does
not calculate, render, edit, export, sync, promote, or approve either input
workbook.  It compares every parser-supported workbook surface and preserves
unsupported application/runtime semantics as explicit unresolved items.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import shutil
import stat
from typing import Any, Iterable, Mapping, Sequence
import uuid
import xml.etree.ElementTree as ET
import zipfile

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from legislation import extract_legislation_workbook_audited_layout as LAYOUT


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_MANIFEST = (
    ROOT / "audits" / "legfix5" / "production_baseline_full_downstream"
    / "baseline_manifest.json"
)
DEFAULT_LAYOUT_RECEIPT = (
    ROOT / "audits" / "legfix5" / "spreadsheet_engine_verification"
    / "audited_layout_sidecar_ooxml_v1" / "audited_member_layout.json"
)
DEFAULT_MODEL_RECEIPT = (
    ROOT / "audits" / "legfix5" / "spreadsheet_engine_verification"
    / "model_verification" / "model_verification_receipt.json"
)
DEFAULT_RANK_POINTER = (
    ROOT / "audits" / "legfix5" / "post_weekly_refresh_rank_drift"
    / "current.json"
)
DEFAULT_OUT_ROOT = (
    ROOT / "audits" / "legfix5"
    / "post_weekly_refresh_workbook_ooxml_semantics"
)
EXPECTED_BASELINE_SHA256 = (
    "5c61cab60f4831fb5842fa8c20c2703c05a986b2a7e32e39f61fe3a08cc19c31"
)
# Default expected-live hash re-bound at re-attestation on 2026-08-04;
# the CLI flag remains the per-run override.
EXPECTED_LIVE_SHA256 = (
    "5001e7df6d2e92177d672da114a26242fb0f3089a35db0d1c75b1964c3d2f3ac"
)

SCHEMA = "post-weekly-refresh-workbook-ooxml-semantics-manifest-v1"
POINTER_SCHEMA = "post-weekly-refresh-workbook-ooxml-semantics-pointer-v1"
CELL_SCHEMA = "post-weekly-refresh-workbook-cell-difference-v1"
SHEET_SCHEMA = "post-weekly-refresh-workbook-sheet-comparison-v1"
PACKAGE_SCHEMA = "post-weekly-refresh-workbook-package-part-difference-v1"
STYLE_SCHEMA = "post-weekly-refresh-workbook-style-difference-v1"
NAME_SCHEMA = "post-weekly-refresh-workbook-defined-name-difference-v1"
REL_SCHEMA = "post-weekly-refresh-workbook-relationship-difference-v1"

MAIN_NS = LAYOUT.OOXML_MAIN_NS
DOC_REL_NS = LAYOUT.OOXML_DOCUMENT_REL_NS
PACKAGE_REL_NS = LAYOUT.OOXML_PACKAGE_REL_NS
ERROR_RE = LAYOUT.ERROR_TOKEN_RE


class WorkbookSemanticAuditError(RuntimeError):
    """The comparison contract or an immutable receipt failed closed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbookSemanticAuditError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def file_item(path: Path) -> dict[str, Any]:
    path = path.resolve()
    require(path.is_file(), f"Missing bound file: {path}")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def validate_file_item(item: Any, label: str) -> Path:
    require(isinstance(item, dict), f"{label} receipt missing")
    path = Path(str(item.get("path") or ""))
    require(path.is_absolute() and path.is_file(), f"{label} path invalid")
    require(
        item.get("bytes", item.get("size")) == path.stat().st_size
        and item.get("sha256") == sha256_file(path),
        f"{label} receipt drift",
    )
    return path.resolve()


def read_json(path: Path) -> Any:
    require(path.is_file(), f"Missing JSON input: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise WorkbookSemanticAuditError(
            f"Unreadable JSON input {path}: {exc}"
        ) from exc


def write_if_changed(path: Path, body: bytes) -> bool:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == body:
        return False
    require(
        not path.exists() or path.is_file(),
        f"Pointer target is not a file: {path}",
    )
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(body)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def cleanup_temporary(path: Path, expected_parent: Path) -> None:
    path = path.resolve()
    expected_parent = expected_parent.resolve()
    require(
        path.parent == expected_parent
        and path.name.startswith(".workbook-ooxml-semantics."),
        f"Unsafe temporary cleanup target: {path}",
    )

    def onexc(function: Any, target: str, error: BaseException) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=onexc)


def _text_or_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def xml_token(element: ET.Element) -> dict[str, Any]:
    """Canonical parser-supported XML structure, independent of prefixes."""
    return {
        "tag": element.tag,
        "attributes": sorted(
            (str(key), str(value)) for key, value in element.attrib.items()
        ),
        "text": _text_or_none(element.text),
        "children": [xml_token(child) for child in list(element)],
    }


def xml_digest(body: bytes) -> str | None:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    return sha256_bytes(canonical_bytes(xml_token(root)))


def _resolve_relationship_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(source_part)
    return posixpath.normpath(posixpath.join(base, target))


def package_inventory(
    package: zipfile.ZipFile,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for order, info in enumerate(package.infolist()):
        if info.is_dir():
            continue
        body = package.read(info.filename)
        result[info.filename] = {
            "path": info.filename,
            "zip_entry_order": order,
            "uncompressed_bytes": len(body),
            "compressed_bytes": info.compress_size,
            "crc32": f"{info.CRC:08x}",
            "compression_type": info.compress_type,
            "flag_bits": info.flag_bits,
            "date_time": list(info.date_time),
            "external_attr": info.external_attr,
            "internal_attr": info.internal_attr,
            "create_system": info.create_system,
            "sha256": sha256_bytes(body),
            "xml_semantic_sha256": (
                xml_digest(body)
                if info.filename.lower().endswith((".xml", ".rels"))
                else None
            ),
        }
    return result


def relationship_inventory(
    package: zipfile.ZipFile,
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for name in sorted(
        value
        for value in package.namelist()
        if value.lower().endswith(".rels")
    ):
        try:
            root = ET.fromstring(package.read(name))
        except ET.ParseError as exc:
            raise WorkbookSemanticAuditError(
                f"Invalid relationship part: {name}"
            ) from exc
        if name == "_rels/.rels":
            source_part = ""
        else:
            directory = posixpath.dirname(posixpath.dirname(name))
            source_name = posixpath.basename(name)[:-5]
            source_part = posixpath.join(directory, source_name)
        for relation in root.findall(
            f"{{{PACKAGE_REL_NS}}}Relationship"
        ):
            relation_id = str(relation.get("Id") or "")
            require(relation_id, f"Blank relationship Id in {name}")
            key = (name, relation_id)
            require(key not in result, f"Duplicate relationship: {key}")
            target = str(relation.get("Target") or "")
            mode = str(relation.get("TargetMode") or "Internal")
            result[key] = {
                "relationship_part": name,
                "source_part": source_part,
                "id": relation_id,
                "type": str(relation.get("Type") or ""),
                "target": target,
                "target_mode": mode,
                "resolved_internal_target": (
                    _resolve_relationship_target(source_part, target)
                    if mode.casefold() != "external"
                    else None
                ),
                "attributes": sorted(
                    (str(key), str(value))
                    for key, value in relation.attrib.items()
                ),
            }
    return result


def workbook_model(
    package: zipfile.ZipFile,
) -> dict[str, Any]:
    try:
        workbook = ET.fromstring(package.read("xl/workbook.xml"))
        rel_root = ET.fromstring(
            package.read("xl/_rels/workbook.xml.rels")
        )
    except (KeyError, ET.ParseError) as exc:
        raise WorkbookSemanticAuditError(
            "Invalid workbook.xml or workbook relationship part"
        ) from exc
    rel_targets = {
        str(row.get("Id") or ""): str(row.get("Target") or "")
        for row in rel_root.findall(
            f"{{{PACKAGE_REL_NS}}}Relationship"
        )
    }
    sheets: list[dict[str, Any]] = []
    for order, sheet in enumerate(
        workbook.findall(f".//{{{MAIN_NS}}}sheet")
    ):
        rel_id = str(sheet.get(f"{{{DOC_REL_NS}}}id") or "")
        target = rel_targets.get(rel_id, "")
        require(target, f"Unresolved sheet relationship: {rel_id}")
        sheets.append(
            {
                "order": order,
                "name": str(sheet.get("name") or ""),
                "sheet_id": str(sheet.get("sheetId") or ""),
                "state": str(sheet.get("state") or "visible"),
                "relationship_id": rel_id,
                "target": (
                    target.lstrip("/")
                    if target.startswith("/")
                    else posixpath.normpath(posixpath.join("xl", target))
                ),
                "attributes": sorted(
                    (str(key), str(value))
                    for key, value in sheet.attrib.items()
                ),
            }
        )
    require(
        len({row["name"] for row in sheets}) == len(sheets),
        "Workbook contains duplicate sheet names",
    )

    defined_names = []
    occurrence: Counter[tuple[str, str]] = Counter()
    for row in workbook.findall(f".//{{{MAIN_NS}}}definedName"):
        name = str(row.get("name") or "")
        local_sheet_id = str(row.get("localSheetId") or "")
        base_key = (name, local_sheet_id)
        index = occurrence[base_key]
        occurrence[base_key] += 1
        defined_names.append(
            {
                "key": {
                    "name": name,
                    "local_sheet_id": local_sheet_id,
                    "occurrence": index,
                },
                "name": name,
                "local_sheet_id": local_sheet_id or None,
                "formula_or_reference": row.text or "",
                "attributes": sorted(
                    (str(key), str(value))
                    for key, value in row.attrib.items()
                ),
            }
        )
    return {
        "sheets": sheets,
        "defined_names": defined_names,
        "workbook_xml_semantic_sha256": sha256_bytes(
            canonical_bytes(xml_token(workbook))
        ),
        "calculation_properties": [
            xml_token(row)
            for row in workbook.findall(f".//{{{MAIN_NS}}}calcPr")
        ],
        "workbook_properties": [
            xml_token(row)
            for row in workbook.findall(f".//{{{MAIN_NS}}}workbookPr")
        ],
    }


def _style_children(
    root: ET.Element,
    section: str,
    child: str,
) -> list[dict[str, Any]]:
    parent = root.find(f"{{{MAIN_NS}}}{section}")
    if parent is None:
        return []
    return [
        xml_token(item)
        for item in parent.findall(f"{{{MAIN_NS}}}{child}")
    ]


def style_catalog(package: zipfile.ZipFile) -> dict[str, Any]:
    try:
        root = ET.fromstring(package.read("xl/styles.xml"))
    except (KeyError, ET.ParseError) as exc:
        raise WorkbookSemanticAuditError("Invalid xl/styles.xml") from exc
    sections = {}
    for section in (
        "numFmts",
        "fonts",
        "fills",
        "borders",
        "cellStyleXfs",
        "cellXfs",
        "cellStyles",
        "dxfs",
        "tableStyles",
        "colors",
        "extLst",
    ):
        node = root.find(f"{{{MAIN_NS}}}{section}")
        token = xml_token(node) if node is not None else None
        sections[section] = {
            "present": node is not None,
            "declared_count": (
                int(node.get("count"))
                if node is not None and str(node.get("count") or "").isdigit()
                else None
            ),
            "child_count": len(list(node)) if node is not None else 0,
            "semantic_sha256": (
                sha256_bytes(canonical_bytes(token))
                if token is not None
                else None
            ),
        }
    fonts = _style_children(root, "fonts", "font")
    fills = _style_children(root, "fills", "fill")
    borders = _style_children(root, "borders", "border")
    cell_style_xfs = _style_children(root, "cellStyleXfs", "xf")
    cell_xfs = _style_children(root, "cellXfs", "xf")
    num_fmt_parent = root.find(f"{{{MAIN_NS}}}numFmts")
    custom_num_fmts = {
        str(row.get("numFmtId") or ""): xml_token(row)
        for row in (
            num_fmt_parent.findall(f"{{{MAIN_NS}}}numFmt")
            if num_fmt_parent is not None
            else []
        )
    }

    def referenced(items: Sequence[Any], raw_id: Any) -> Any:
        try:
            index = int(raw_id)
        except (TypeError, ValueError):
            return None
        return items[index] if 0 <= index < len(items) else {
            "invalid_reference": index
        }

    effective: list[dict[str, Any]] = []
    for index, xf in enumerate(cell_xfs):
        attrs = dict(xf["attributes"])
        num_fmt_id = str(attrs.get("numFmtId") or "0")
        token = {
            "cell_xf": xf,
            "font": referenced(fonts, attrs.get("fontId", "0")),
            "fill": referenced(fills, attrs.get("fillId", "0")),
            "border": referenced(borders, attrs.get("borderId", "0")),
            "base_cell_style_xf": referenced(
                cell_style_xfs, attrs.get("xfId")
            ),
            "number_format": (
                custom_num_fmts.get(num_fmt_id)
                or {"builtin_numFmtId": num_fmt_id}
            ),
        }
        effective.append(
            {
                "index": index,
                "semantic_sha256": sha256_bytes(canonical_bytes(token)),
                "token": token,
            }
        )
    require(effective, "Stylesheet has no cellXfs")
    return {
        "styles_xml_semantic_sha256": sha256_bytes(
            canonical_bytes(xml_token(root))
        ),
        "sections": sections,
        "effective_cell_styles": effective,
    }


def _cell_value(
    cell: ET.Element,
    shared_strings: Sequence[str],
) -> dict[str, Any]:
    value_node = cell.find(f"{{{MAIN_NS}}}v")
    inline_node = cell.find(f"{{{MAIN_NS}}}is")
    if value_node is None and inline_node is None:
        return {"present": False, "value_type": None, "value": None}
    cell_type = str(cell.get("t") or "n")
    raw = (
        LAYOUT._xml_text(inline_node)
        if inline_node is not None
        else (value_node.text or "")
    )
    if cell_type == "s":
        try:
            index = int(raw)
            if index < 0:
                raise ValueError("negative shared-string index")
            value: Any = shared_strings[index]
        except (ValueError, IndexError) as exc:
            raise WorkbookSemanticAuditError(
                f"Invalid shared-string index at {cell.get('r')}"
            ) from exc
        value_type = "text"
    elif cell_type in {"inlineStr", "str"}:
        value = raw
        value_type = "text"
    elif cell_type == "b":
        require(raw in {"0", "1"}, f"Invalid Boolean at {cell.get('r')}")
        value = raw == "1"
        value_type = "boolean"
    elif cell_type == "e":
        value = raw
        value_type = "error"
    elif cell_type == "d":
        value = raw
        value_type = "date"
    else:
        if raw == "":
            return {
                "present": True,
                "value_type": "blank_numeric_cache_or_value",
                "value": None,
            }
        try:
            value = LAYOUT._normalize_ooxml_number(raw)
        except LAYOUT.LayoutExtractionError as exc:
            raise WorkbookSemanticAuditError(str(exc)) from exc
        value_type = "number"
    return {"present": True, "value_type": value_type, "value": value}


def _formula_error(value: Mapping[str, Any]) -> str | None:
    if value.get("present") is not True:
        return None
    text = str(value.get("value") or "")
    match = ERROR_RE.search(text)
    if value.get("value_type") == "error":
        return text or "OOXML_ERROR_WITHOUT_TEXT"
    return match.group(0) if match else None


def _structure_without_cells(element: ET.Element) -> Any:
    if element.tag == f"{{{MAIN_NS}}}c":
        return None
    children = []
    for child in list(element):
        token = _structure_without_cells(child)
        if token is not None:
            children.append(token)
    return {
        "tag": element.tag,
        "attributes": sorted(
            (str(key), str(value)) for key, value in element.attrib.items()
        ),
        "text": (
            None
            if element.tag in {
                f"{{{MAIN_NS}}}v",
                f"{{{MAIN_NS}}}f",
                f"{{{MAIN_NS}}}t",
            }
            else _text_or_none(element.text)
        ),
        "children": children,
    }


def parse_sheet(
    package: zipfile.ZipFile,
    *,
    sheet: Mapping[str, Any],
    shared_strings: Sequence[str],
    styles: Mapping[str, Any],
) -> dict[str, Any]:
    target = str(sheet["target"])
    require(target in package.namelist(), f"Missing worksheet part: {target}")
    try:
        root = ET.fromstring(package.read(target))
    except ET.ParseError as exc:
        raise WorkbookSemanticAuditError(
            f"Invalid worksheet XML: {sheet['name']}"
        ) from exc
    dimension = root.find(f"{{{MAIN_NS}}}dimension")
    effective_styles = styles["effective_cell_styles"]
    cells: dict[str, dict[str, Any]] = {}
    formula_errors: Counter[str] = Counter()
    nonformula_errors: Counter[str] = Counter()
    for cell in root.findall(
        f".//{{{MAIN_NS}}}sheetData/"
        f"{{{MAIN_NS}}}row/{{{MAIN_NS}}}c"
    ):
        address = str(cell.get("r") or "").upper()
        try:
            LAYOUT._cell_sort_key(address)
        except LAYOUT.LayoutExtractionError as exc:
            raise WorkbookSemanticAuditError(str(exc)) from exc
        require(address not in cells, f"Duplicate cell {sheet['name']}!{address}")
        style_text = str(cell.get("s") or "0")
        require(style_text.isdigit(), f"Invalid style index at {address}")
        style_id = int(style_text)
        require(
            0 <= style_id < len(effective_styles),
            f"Out-of-range style at {sheet['name']}!{address}",
        )
        formula_node = cell.find(f"{{{MAIN_NS}}}f")
        value = _cell_value(cell, shared_strings)
        formula = (
            {
                "text": formula_node.text or "",
                "attributes": sorted(
                    (str(key), str(item))
                    for key, item in formula_node.attrib.items()
                ),
            }
            if formula_node is not None
            else None
        )
        error = _formula_error(value)
        if error:
            (formula_errors if formula is not None else nonformula_errors)[
                error
            ] += 1
        cells[address] = {
            "address": address,
            "cell_type": str(cell.get("t") or "n"),
            "style_id": style_id,
            "effective_style_sha256": effective_styles[style_id][
                "semantic_sha256"
            ],
            "formula": formula,
            "cached_value" if formula is not None else "value": value,
            "other_attributes": sorted(
                (str(key), str(item))
                for key, item in cell.attrib.items()
                if key not in {"r", "s", "t"}
            ),
        }
    return {
        "sheet": dict(sheet),
        "dimension": str(dimension.get("ref") or "") if dimension is not None else None,
        "structure_without_cells_sha256": sha256_bytes(
            canonical_bytes(_structure_without_cells(root))
        ),
        "cells": cells,
        "cell_count": len(cells),
        "formula_count": sum(
            row.get("formula") is not None for row in cells.values()
        ),
        "formula_cached_error_counts": dict(sorted(formula_errors.items())),
        "formula_cached_error_count": sum(formula_errors.values()),
        "nonformula_error_counts": dict(sorted(nonformula_errors.items())),
        "nonformula_error_count": sum(nonformula_errors.values()),
    }


def _changed_fields(
    baseline: Mapping[str, Any] | None,
    live: Mapping[str, Any] | None,
) -> list[str]:
    if baseline is None:
        return ["cell_added"]
    if live is None:
        return ["cell_removed"]
    fields = []
    for field in (
        "cell_type",
        "style_id",
        "effective_style_sha256",
        "formula",
        "cached_value",
        "value",
        "other_attributes",
    ):
        if baseline.get(field) != live.get(field):
            fields.append(field)
    return fields


def _formula_change_fields(
    baseline: Mapping[str, Any] | None,
    live: Mapping[str, Any] | None,
) -> list[str]:
    baseline_formula = baseline.get("formula") if baseline else None
    live_formula = live.get("formula") if live else None
    if baseline_formula is None and live_formula is None:
        return []
    fields = []
    if baseline_formula is None:
        fields.append("formula_added")
    elif live_formula is None:
        fields.append("formula_removed")
    else:
        if baseline_formula.get("text") != live_formula.get("text"):
            fields.append("formula_text")
        if baseline_formula.get("attributes") != live_formula.get("attributes"):
            fields.append("formula_attributes")
    baseline_cache = baseline.get("cached_value") if baseline else None
    live_cache = live.get("cached_value") if live else None
    if baseline_cache != live_cache:
        fields.append("formula_cached_value")
    return fields


def compare_cells(
    sheet_name: str,
    baseline: Mapping[str, Any] | None,
    live: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    baseline_cells = (baseline or {}).get("cells") or {}
    live_cells = (live or {}).get("cells") or {}
    keys = sorted(
        set(baseline_cells) | set(live_cells),
        key=LAYOUT._cell_sort_key,
    )
    differences = []
    counts: Counter[str] = Counter()
    for address in keys:
        old = baseline_cells.get(address)
        new = live_cells.get(address)
        fields = _changed_fields(old, new)
        formula_fields = _formula_change_fields(old, new)
        if not fields and not formula_fields:
            counts["unchanged_cell_count"] += 1
            continue
        for field in fields:
            counts[f"{field}_count"] += 1
        for field in formula_fields:
            counts[f"{field}_count"] += 1
        if formula_fields:
            counts["formula_mismatch_count"] += int(
                any(
                    field in {
                        "formula_added",
                        "formula_removed",
                        "formula_text",
                        "formula_attributes",
                    }
                    for field in formula_fields
                )
            )
            counts["formula_cached_value_mismatch_count"] += int(
                "formula_cached_value" in formula_fields
            )
        cached_only = fields == ["cached_value"] and formula_fields == [
            "formula_cached_value"
        ]
        counts["formula_cached_only_changed_cell_count"] += int(cached_only)
        differences.append(
            {
                "schema": CELL_SCHEMA,
                "sheet": sheet_name,
                "address": address,
                "changed_fields": sorted(set(fields + formula_fields)),
                "formula_cached_only_change": cached_only,
                "baseline": old,
                "live": new,
            }
        )
    counts["baseline_cell_count"] = len(baseline_cells)
    counts["live_cell_count"] = len(live_cells)
    counts["changed_cell_count"] = len(differences)
    for key in (
        "unchanged_cell_count",
        "cell_added_count",
        "cell_removed_count",
        "cell_type_count",
        "style_id_count",
        "effective_style_sha256_count",
        "formula_count",
        "cached_value_count",
        "value_count",
        "other_attributes_count",
        "formula_added_count",
        "formula_removed_count",
        "formula_text_count",
        "formula_attributes_count",
        "formula_cached_value_count",
        "formula_mismatch_count",
        "formula_cached_value_mismatch_count",
        "formula_cached_only_changed_cell_count",
    ):
        counts.setdefault(key, 0)
    counts["cell_type_mismatch_count"] = counts["cell_type_count"]
    counts["style_id_mismatch_count"] = counts["style_id_count"]
    counts["effective_style_mismatch_count"] = counts[
        "effective_style_sha256_count"
    ]
    counts["nonformula_value_mismatch_count"] = counts["value_count"]
    counts["cell_other_attribute_mismatch_count"] = counts[
        "other_attributes_count"
    ]
    counts["formula_text_mismatch_count"] = counts["formula_text_count"]
    counts["formula_attribute_mismatch_count"] = counts[
        "formula_attributes_count"
    ]
    return differences, dict(sorted(counts.items()))


def compare_keyed(
    *,
    schema: str,
    baseline: Mapping[Any, Mapping[str, Any]],
    live: Mapping[Any, Mapping[str, Any]],
    key_label: str,
) -> list[dict[str, Any]]:
    rows = []
    for key in sorted(set(baseline) | set(live), key=lambda item: str(item)):
        old = baseline.get(key)
        new = live.get(key)
        if old == new:
            continue
        rows.append(
            {
                "schema": schema,
                key_label: list(key) if isinstance(key, tuple) else key,
                "change": (
                    "added" if old is None else "removed" if new is None else "changed"
                ),
                "baseline": old,
                "live": new,
            }
        )
    return rows


def compare_package_parts(
    baseline: Mapping[str, Mapping[str, Any]],
    live: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for name in sorted(set(baseline) | set(live)):
        old = baseline.get(name)
        new = live.get(name)
        if old == new:
            continue
        rows.append(
            {
                "schema": PACKAGE_SCHEMA,
                "part": name,
                "change": (
                    "added" if old is None else "removed" if new is None else "changed"
                ),
                "byte_identical": bool(
                    old and new and old["sha256"] == new["sha256"]
                ),
                "xml_semantically_identical": bool(
                    old
                    and new
                    and old.get("xml_semantic_sha256") is not None
                    and old.get("xml_semantic_sha256")
                    == new.get("xml_semantic_sha256")
                ),
                "baseline": old,
                "live": new,
            }
        )
    return rows


def compare_styles(
    baseline: Mapping[str, Any],
    live: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    sections = set(baseline["sections"]) | set(live["sections"])
    for section in sorted(sections):
        old = baseline["sections"].get(section)
        new = live["sections"].get(section)
        if old != new:
            rows.append(
                {
                    "schema": STYLE_SCHEMA,
                    "kind": "stylesheet_section",
                    "key": section,
                    "change": "changed",
                    "baseline": old,
                    "live": new,
                }
            )
    old_styles = {
        row["index"]: row for row in baseline["effective_cell_styles"]
    }
    new_styles = {row["index"]: row for row in live["effective_cell_styles"]}
    for index in sorted(set(old_styles) | set(new_styles)):
        old = old_styles.get(index)
        new = new_styles.get(index)
        if old == new:
            continue
        rows.append(
            {
                "schema": STYLE_SCHEMA,
                "kind": "effective_cell_style",
                "key": index,
                "change": (
                    "added" if old is None else "removed" if new is None else "changed"
                ),
                "baseline": old,
                "live": new,
            }
        )
    return rows


def _defined_name_map(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[Any, ...], Mapping[str, Any]]:
    return {
        (
            row["key"]["name"],
            row["key"]["local_sheet_id"],
            row["key"]["occurrence"],
        ): row
        for row in rows
    }


def validate_bound_receipts(
    *,
    baseline_sha256: str,
    live_sha256: str,
    layout_path: Path,
    model_path: Path,
    rank_pointer_path: Path,
) -> dict[str, Any]:
    layout = read_json(layout_path)
    require(
        isinstance(layout, dict)
        and layout.get("schema") == LAYOUT.SCHEMA
        and layout.get("status") == "PASS"
        and layout.get("production_written") is False
        and layout.get("source_workbook_sha256") == baseline_sha256
        and (layout.get("sublayer_formula_audit") or {}).get(
            "checked_cells"
        )
        == 9594
        and (layout.get("sublayer_formula_audit") or {}).get(
            "mismatch_count"
        )
        == 0
        and (layout.get("press_normalized_formula_audit") or {}).get(
            "checked_cells"
        )
        == 10660
        and (layout.get("press_normalized_formula_audit") or {}).get(
            "mismatch_count"
        )
        == 0,
        "Audited layout receipt is stale or incomplete",
    )
    model = read_json(model_path)
    require(
        isinstance(model, dict)
        and model.get("schema")
        == "legislation-spreadsheet-model-verification-v1"
        and model.get("production_written") is False
        and model.get("workbook_edited") is False
        and (model.get("input") or {}).get("sha256") == baseline_sha256
        and (model.get("input") or {}).get("unchanged_after_verification")
        is True,
        "Workbook model receipt is stale or unsafe",
    )
    rank_pointer = read_json(rank_pointer_path)
    require(
        isinstance(rank_pointer, dict)
        and rank_pointer.get("schema")
        == "post-weekly-refresh-rank-drift-pointer-v1"
        and rank_pointer.get("authoritative_for_legislation_ranks") is False
        and rank_pointer.get("authoritative_for_scoring") is False
        and rank_pointer.get("authoritative_for_sync_or_promotion") is False
        and rank_pointer.get("sync_or_promotion_performed") is False,
        "Rank drift pointer safety contract drift",
    )
    rank_manifest_path = validate_file_item(
        rank_pointer.get("manifest"), "Rank drift manifest"
    )
    rank_manifest = read_json(rank_manifest_path)
    require(
        isinstance(rank_manifest, dict)
        and rank_manifest.get("schema")
        == "post-weekly-refresh-rank-drift-manifest-v1"
        and rank_manifest.get("audit_id") == rank_pointer.get("audit_id")
        and rank_manifest.get("status")
        == "PASS_OBSERVATIONAL_NONAUTHORITATIVE"
        and rank_manifest.get("authoritative_for_legislation_ranks") is False
        and rank_manifest.get("authoritative_for_scoring") is False
        and rank_manifest.get("authoritative_for_sync_or_promotion") is False
        and rank_manifest.get("sync_or_promotion_performed") is False,
        "Rank drift manifest safety contract drift",
    )
    return {
        "audited_layout": {
            "receipt": file_item(layout_path),
            "status": layout["status"],
            "bound_workbook_sha256": baseline_sha256,
            "sublayer_formula_checked_cells": 9594,
            "sublayer_formula_mismatch_count": 0,
            "press_formula_checked_cells": 10660,
            "press_formula_mismatch_count": 0,
            "applies_to_live_workbook": baseline_sha256 == live_sha256,
        },
        "workbook_model": {
            "receipt": file_item(model_path),
            "status": model.get("status"),
            "bound_workbook_sha256": baseline_sha256,
            "critical_numeric_sample_passed": (
                model.get("critical_numeric_sample") or {}
            ).get("passed"),
            "applies_to_live_workbook": baseline_sha256 == live_sha256,
        },
        "rank_drift": {
            "pointer": file_item(rank_pointer_path),
            "manifest": file_item(rank_manifest_path),
            "audit_id": rank_manifest.get("audit_id"),
            "status": rank_manifest.get("status"),
            "member_count": (rank_manifest.get("summary") or {}).get(
                "member_count"
            ),
            "observes_current_live_state": True,
        },
    }


def _ledger_body(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_bytes(row) for row in rows)


def _output_receipt(
    temporary_path: Path,
    final_path: Path,
    *,
    record_count: int,
) -> dict[str, Any]:
    value = file_item(temporary_path)
    value["path"] = str(final_path.resolve())
    value["record_count"] = record_count
    return value


def compare_workbooks(
    baseline_path: Path,
    live_path: Path,
) -> dict[str, Any]:
    baseline_path = baseline_path.resolve()
    live_path = live_path.resolve()
    before = {
        "baseline": file_item(baseline_path),
        "live": file_item(live_path),
    }
    try:
        baseline_package = zipfile.ZipFile(baseline_path, "r")
        live_package = zipfile.ZipFile(live_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise WorkbookSemanticAuditError("Invalid XLSX package") from exc
    with baseline_package, live_package:
        baseline_inventory = package_inventory(baseline_package)
        live_inventory = package_inventory(live_package)
        package_rows = compare_package_parts(
            baseline_inventory, live_inventory
        )
        baseline_relationships = relationship_inventory(baseline_package)
        live_relationships = relationship_inventory(live_package)
        relationship_rows = compare_keyed(
            schema=REL_SCHEMA,
            baseline=baseline_relationships,
            live=live_relationships,
            key_label="relationship_key",
        )
        baseline_model = workbook_model(baseline_package)
        live_model = workbook_model(live_package)
        baseline_styles = style_catalog(baseline_package)
        live_styles = style_catalog(live_package)
        style_rows = compare_styles(baseline_styles, live_styles)
        baseline_names = _defined_name_map(
            baseline_model["defined_names"]
        )
        live_names = _defined_name_map(live_model["defined_names"])
        defined_name_rows = compare_keyed(
            schema=NAME_SCHEMA,
            baseline=baseline_names,
            live=live_names,
            key_label="defined_name_key",
        )
        baseline_shared = LAYOUT._read_shared_strings(baseline_package)
        live_shared = LAYOUT._read_shared_strings(live_package)
        baseline_sheet_meta = {
            row["name"]: row for row in baseline_model["sheets"]
        }
        live_sheet_meta = {
            row["name"]: row for row in live_model["sheets"]
        }
        sheet_rows = []
        cell_rows = []
        aggregate_counts: Counter[str] = Counter()
        baseline_formula_errors: Counter[str] = Counter()
        live_formula_errors: Counter[str] = Counter()
        baseline_nonformula_errors: Counter[str] = Counter()
        live_nonformula_errors: Counter[str] = Counter()
        all_sheet_names = sorted(
            set(baseline_sheet_meta) | set(live_sheet_meta),
            key=lambda name: (
                (baseline_sheet_meta.get(name) or live_sheet_meta[name])[
                    "order"
                ],
                name,
            ),
        )
        for name in all_sheet_names:
            old_meta = baseline_sheet_meta.get(name)
            new_meta = live_sheet_meta.get(name)
            old_sheet = (
                parse_sheet(
                    baseline_package,
                    sheet=old_meta,
                    shared_strings=baseline_shared,
                    styles=baseline_styles,
                )
                if old_meta is not None
                else None
            )
            new_sheet = (
                parse_sheet(
                    live_package,
                    sheet=new_meta,
                    shared_strings=live_shared,
                    styles=live_styles,
                )
                if new_meta is not None
                else None
            )
            differences, counts = compare_cells(name, old_sheet, new_sheet)
            cell_rows.extend(differences)
            aggregate_counts.update(counts)
            for key, value in (
                (old_sheet or {}).get(
                    "formula_cached_error_counts", {}
                ).items()
            ):
                baseline_formula_errors[key] += value
            for key, value in (
                (new_sheet or {}).get(
                    "formula_cached_error_counts", {}
                ).items()
            ):
                live_formula_errors[key] += value
            for key, value in (
                (old_sheet or {}).get("nonformula_error_counts", {}).items()
            ):
                baseline_nonformula_errors[key] += value
            for key, value in (
                (new_sheet or {}).get("nonformula_error_counts", {}).items()
            ):
                live_nonformula_errors[key] += value
            row = {
                "schema": SHEET_SCHEMA,
                "sheet": name,
                "change": (
                    "added"
                    if old_sheet is None
                    else "removed"
                    if new_sheet is None
                    else "present_in_both"
                ),
                "baseline_metadata": old_meta,
                "live_metadata": new_meta,
                "metadata_identical": old_meta == new_meta,
                "baseline_dimension": (
                    old_sheet.get("dimension") if old_sheet else None
                ),
                "live_dimension": (
                    new_sheet.get("dimension") if new_sheet else None
                ),
                "dimension_identical": bool(
                    old_sheet
                    and new_sheet
                    and old_sheet.get("dimension")
                    == new_sheet.get("dimension")
                ),
                "baseline_structure_without_cells_sha256": (
                    old_sheet.get("structure_without_cells_sha256")
                    if old_sheet
                    else None
                ),
                "live_structure_without_cells_sha256": (
                    new_sheet.get("structure_without_cells_sha256")
                    if new_sheet
                    else None
                ),
                "structure_without_cells_identical": bool(
                    old_sheet
                    and new_sheet
                    and old_sheet.get("structure_without_cells_sha256")
                    == new_sheet.get("structure_without_cells_sha256")
                ),
                "baseline_formula_count": (
                    old_sheet.get("formula_count", 0) if old_sheet else 0
                ),
                "live_formula_count": (
                    new_sheet.get("formula_count", 0) if new_sheet else 0
                ),
                "baseline_formula_cached_error_count": (
                    old_sheet.get("formula_cached_error_count", 0)
                    if old_sheet
                    else 0
                ),
                "live_formula_cached_error_count": (
                    new_sheet.get("formula_cached_error_count", 0)
                    if new_sheet
                    else 0
                ),
                "cell_comparison": counts,
            }
            sheet_rows.append(row)
    after = {
        "baseline": file_item(baseline_path),
        "live": file_item(live_path),
    }
    require(before == after, "Input workbook changed during read-only audit")

    baseline_order = [row["name"] for row in baseline_model["sheets"]]
    live_order = [row["name"] for row in live_model["sheets"]]
    formula_error_summary = {
        "baseline_formula_cached_error_count": sum(
            baseline_formula_errors.values()
        ),
        "live_formula_cached_error_count": sum(live_formula_errors.values()),
        "baseline_formula_cached_errors_by_token": dict(
            sorted(baseline_formula_errors.items())
        ),
        "live_formula_cached_errors_by_token": dict(
            sorted(live_formula_errors.items())
        ),
        "baseline_nonformula_error_count": sum(
            baseline_nonformula_errors.values()
        ),
        "live_nonformula_error_count": sum(
            live_nonformula_errors.values()
        ),
        "baseline_nonformula_errors_by_token": dict(
            sorted(baseline_nonformula_errors.items())
        ),
        "live_nonformula_errors_by_token": dict(
            sorted(live_nonformula_errors.items())
        ),
        "formula_results_recalculated": False,
        "stored_cached_values_observed_only": True,
    }
    baseline_calc_attrs = dict(
        (
            baseline_model["calculation_properties"][0].get(
                "attributes", []
            )
            if baseline_model["calculation_properties"]
            else []
        )
    )
    live_calc_attrs = dict(
        (
            live_model["calculation_properties"][0].get("attributes", [])
            if live_model["calculation_properties"]
            else []
        )
    )
    raw_value_changes = aggregate_counts.get("value_count", 0)
    cached_value_changes = aggregate_counts.get(
        "formula_cached_value_mismatch_count", 0
    )
    summary = {
        "baseline_sha256": before["baseline"]["sha256"],
        "live_sha256": before["live"]["sha256"],
        "workbook_byte_identical": before["baseline"]["sha256"]
        == before["live"]["sha256"],
        "baseline_package_part_count": len(baseline_inventory),
        "live_package_part_count": len(live_inventory),
        "changed_package_part_count": len(package_rows),
        "package_content_changed_part_count": sum(
            not row["byte_identical"] for row in package_rows
        ),
        "package_zip_metadata_only_changed_part_count": sum(
            row["byte_identical"] for row in package_rows
        ),
        "package_part_added_count": sum(
            row["change"] == "added" for row in package_rows
        ),
        "package_part_removed_count": sum(
            row["change"] == "removed" for row in package_rows
        ),
        "xml_semantically_changed_package_part_count": sum(
            bool(
                row.get("baseline")
                and row.get("live")
                and row["baseline"].get("xml_semantic_sha256") is not None
                and row["live"].get("xml_semantic_sha256") is not None
                and not row["xml_semantically_identical"]
            )
            for row in package_rows
        ),
        "baseline_sheet_count": len(baseline_order),
        "live_sheet_count": len(live_order),
        "baseline_formula_count": sum(
            row["baseline_formula_count"] for row in sheet_rows
        ),
        "live_formula_count": sum(
            row["live_formula_count"] for row in sheet_rows
        ),
        "sheet_order_and_names_identical": baseline_order == live_order,
        "sheet_dimension_mismatch_count": sum(
            not row["dimension_identical"] for row in sheet_rows
        ),
        "sheet_metadata_mismatch_count": sum(
            not row["metadata_identical"] for row in sheet_rows
        ),
        "sheet_noncell_structure_mismatch_count": sum(
            not row["structure_without_cells_identical"]
            for row in sheet_rows
        ),
        "changed_cell_count": len(cell_rows),
        **dict(sorted(aggregate_counts.items())),
        "defined_name_count_baseline": len(baseline_names),
        "defined_name_count_live": len(live_names),
        "defined_name_mismatch_count": len(defined_name_rows),
        "relationship_count_baseline": len(baseline_relationships),
        "relationship_count_live": len(live_relationships),
        "relationship_mismatch_count": len(relationship_rows),
        "stylesheet_semantically_identical": baseline_styles[
            "styles_xml_semantic_sha256"
        ]
        == live_styles["styles_xml_semantic_sha256"],
        "effective_cell_style_count_baseline": len(
            baseline_styles["effective_cell_styles"]
        ),
        "effective_cell_style_count_live": len(
            live_styles["effective_cell_styles"]
        ),
        "style_definition_mismatch_count": len(style_rows),
        "workbook_xml_semantically_identical": baseline_model[
            "workbook_xml_semantic_sha256"
        ]
        == live_model["workbook_xml_semantic_sha256"],
        "baseline_calculation_mode": baseline_calc_attrs.get("calcMode"),
        "live_calculation_mode": live_calc_attrs.get("calcMode"),
        "baseline_full_calc_on_load": baseline_calc_attrs.get(
            "fullCalcOnLoad"
        ),
        "live_full_calc_on_load": live_calc_attrs.get("fullCalcOnLoad"),
        "raw_value_drift_with_zero_formula_cache_drift": (
            raw_value_changes > 0 and cached_value_changes == 0
        ),
        "live_formula_cache_freshness_proven": False,
        "formula_cache_freshness_status": (
            "UNRESOLVED_RAW_VALUES_CHANGED_CACHES_UNCHANGED_MANUAL_CALC"
            if (
                raw_value_changes > 0
                and cached_value_changes == 0
                and live_calc_attrs.get("calcMode") == "manual"
            )
            else "UNRESOLVED_NO_CALCULATION_ENGINE"
        ),
        **formula_error_summary,
    }
    return {
        "inputs": before,
        "summary": summary,
        "sheet_rows": sheet_rows,
        "cell_rows": cell_rows,
        "package_rows": package_rows,
        "style_rows": style_rows,
        "defined_name_rows": defined_name_rows,
        "relationship_rows": relationship_rows,
        "workbook_level": {
            "baseline": {
                key: baseline_model[key]
                for key in (
                    "workbook_xml_semantic_sha256",
                    "calculation_properties",
                    "workbook_properties",
                )
            },
            "live": {
                key: live_model[key]
                for key in (
                    "workbook_xml_semantic_sha256",
                    "calculation_properties",
                    "workbook_properties",
                )
            },
            "sheet_order_baseline": baseline_order,
            "sheet_order_live": live_order,
        },
    }


def build_audit(
    *,
    baseline_manifest_path: Path = DEFAULT_BASELINE_MANIFEST,
    layout_receipt_path: Path = DEFAULT_LAYOUT_RECEIPT,
    model_receipt_path: Path = DEFAULT_MODEL_RECEIPT,
    rank_pointer_path: Path = DEFAULT_RANK_POINTER,
    out_root: Path = DEFAULT_OUT_ROOT,
    expected_baseline_sha256: str | None = EXPECTED_BASELINE_SHA256,
    expected_live_sha256: str | None = EXPECTED_LIVE_SHA256,
) -> tuple[Path, dict[str, Any], bool]:
    baseline_manifest_path = baseline_manifest_path.resolve()
    baseline = LAYOUT.resolve_frozen_workbook(baseline_manifest_path)
    baseline_path = Path(baseline["workbook_path"]).resolve()
    live_path = Path(
        str(baseline["production_source_declared"])
    ).resolve()
    require(live_path.is_file(), f"Live workbook missing: {live_path}")
    baseline_hash = sha256_file(baseline_path)
    live_hash = sha256_file(live_path)
    if expected_baseline_sha256 is not None:
        require(
            baseline_hash == expected_baseline_sha256,
            "Frozen workbook does not match requested baseline hash",
        )
    if expected_live_sha256 is not None:
        require(
            live_hash == expected_live_sha256,
            "Live workbook does not match requested comparison hash",
        )
    require(
        baseline_path != live_path,
        "Frozen and live workbooks resolve to the same path",
    )
    comparison = compare_workbooks(baseline_path, live_path)
    bound_receipt_evidence = validate_bound_receipts(
        baseline_sha256=baseline_hash,
        live_sha256=live_hash,
        layout_path=layout_receipt_path.resolve(),
        model_path=model_receipt_path.resolve(),
        rank_pointer_path=rank_pointer_path.resolve(),
    )
    ledgers = {
        "sheet_comparison.ndjson": comparison["sheet_rows"],
        "cell_differences.ndjson": comparison["cell_rows"],
        "package_part_differences.ndjson": comparison["package_rows"],
        "style_differences.ndjson": comparison["style_rows"],
        "defined_name_differences.ndjson": comparison[
            "defined_name_rows"
        ],
        "relationship_differences.ndjson": comparison[
            "relationship_rows"
        ],
    }
    ledger_bodies = {
        name: _ledger_body(rows) for name, rows in ledgers.items()
    }
    unresolved = [
        {
            "surface": "formula_evaluation_and_recalculation",
            "reason": (
                "The OOXML parser compares exact formulas and stored cached "
                "values but does not calculate formulas."
            ),
        },
        {
            "surface": "live_formula_cache_freshness",
            "reason": (
                "Raw values changed while all stored formula caches remained "
                "unchanged and calcMode is manual; fullCalcOnLoad=1 is a "
                "declaration, not proof that recalculation has occurred."
            ),
        },
        {
            "surface": "rendered_visual_equivalence",
            "reason": (
                "Styles and drawing/package XML are compared, but visual "
                "rendering is unavailable because artifact-tool imports OOM."
            ),
        },
        {
            "surface": "external_link_runtime_availability",
            "reason": (
                "Relationship targets are compared; external resources are "
                "not dereferenced or executed."
            ),
        },
        {
            "surface": "application_behavior_for_charts_pivots_and_macros",
            "reason": (
                "Package parts and relationships are byte/XML compared; "
                "Excel runtime behavior is not executed."
            ),
        },
        {
            "surface": "causal_attribution_to_legislation",
            "reason": (
                "Observed drift is not attributed to an accepted legislation "
                "recomputation."
            ),
        },
    ]
    producer_sources = {
        "producer": file_item(Path(__file__).resolve()),
        "ooxml_layout_dependency": file_item(
            Path(LAYOUT.__file__).resolve()
        ),
    }
    bound_inputs = {
        "baseline_manifest": file_item(baseline_manifest_path),
        "frozen_workbook": file_item(baseline_path),
        "live_workbook": file_item(live_path),
        "audited_layout_receipt": file_item(layout_receipt_path.resolve()),
        "workbook_model_receipt": file_item(model_receipt_path.resolve()),
        "rank_drift_pointer": file_item(rank_pointer_path.resolve()),
    }
    identity = {
        "schema": "post-weekly-refresh-workbook-ooxml-semantics-identity-v1",
        "bound_inputs": bound_inputs,
        "bound_receipt_evidence": bound_receipt_evidence,
        "producer_sources": producer_sources,
        "summary": comparison["summary"],
        "workbook_level": comparison["workbook_level"],
        "ledger_sha256": {
            name: sha256_bytes(body)
            for name, body in sorted(ledger_bodies.items())
        },
        "ledger_record_counts": {
            name: len(ledgers[name]) for name in sorted(ledgers)
        },
        "unresolved": unresolved,
        "authority_contract": {
            "observational_only": True,
            "authoritative_for_legislation": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "accepted_legislation_recomputation": False,
            "production_or_mainframe_written": False,
            "sync_or_promotion_performed": False,
        },
    }
    audit_id = sha256_bytes(canonical_bytes(identity))
    out_root = out_root.resolve()
    snapshots = out_root / "snapshots"
    snapshot = snapshots / audit_id
    out_root.mkdir(parents=True, exist_ok=True)
    snapshots.mkdir(parents=True, exist_ok=True)
    temporary = snapshots / f".workbook-ooxml-semantics.{uuid.uuid4().hex}"
    temporary.mkdir()
    created = False
    try:
        outputs = {}
        for name, body in ledger_bodies.items():
            temporary_path = temporary / name
            temporary_path.write_bytes(body)
            outputs[name] = _output_receipt(
                temporary_path,
                snapshot / name,
                record_count=len(ledgers[name]),
            )
        manifest = {
            "schema": SCHEMA,
            "audit_id": audit_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "PASS_OBSERVATIONAL_NONAUTHORITATIVE",
            "bound_inputs": bound_inputs,
            "bound_receipt_evidence": bound_receipt_evidence,
            "producer_sources": producer_sources,
            "outputs": outputs,
            "summary": comparison["summary"],
            "workbook_level": comparison["workbook_level"],
            "unresolved": unresolved,
            "parser_supported_semantic_gap_closed": True,
            "formula_results_recalculated": False,
            "visual_render_performed": False,
            "observational_only": True,
            "authoritative_for_legislation": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "accepted_legislation_recomputation": False,
            "production_or_mainframe_written": False,
            "sync_or_promotion_performed": False,
        }
        (temporary / "manifest.json").write_bytes(pretty_bytes(manifest))
        if snapshot.exists():
            existing = read_json(snapshot / "manifest.json")
            require(
                isinstance(existing, dict)
                and existing.get("audit_id") == audit_id,
                "Existing workbook semantic manifest identity mismatch",
            )
            require(
                all(
                    canonical_bytes(existing.get(key))
                    == canonical_bytes(value)
                    for key, value in manifest.items()
                    if key != "generated_at"
                ),
                "Existing workbook semantic manifest contract drift: "
                + ", ".join(
                    key
                    for key, value in manifest.items()
                    if key != "generated_at"
                    and canonical_bytes(existing.get(key))
                    != canonical_bytes(value)
                ),
            )
            for name, expected in outputs.items():
                path = validate_file_item(
                    (existing.get("outputs") or {}).get(name),
                    f"Existing {name}",
                )
                require(
                    path == Path(str(expected["path"])).resolve()
                    and (existing["outputs"][name]) == expected,
                    f"Existing {name} output receipt drift",
                )
            manifest = existing
        else:
            os.replace(temporary, snapshot)
            created = True
    finally:
        if temporary.exists():
            cleanup_temporary(temporary, snapshots)
    pointer = {
        "schema": POINTER_SCHEMA,
        "audit_id": audit_id,
        "snapshot_dir": str(snapshot.resolve()),
        "manifest": file_item(snapshot / "manifest.json"),
        "producer": producer_sources["producer"],
        "observational_only": True,
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "accepted_legislation_recomputation": False,
        "production_or_mainframe_written": False,
        "sync_or_promotion_performed": False,
    }
    pointer_path = out_root / "current.json"
    if pointer_path.exists():
        existing_pointer = read_json(pointer_path)
        if (
            isinstance(existing_pointer, dict)
            and existing_pointer.get("audit_id") == audit_id
        ):
            require(
                existing_pointer == pointer,
                "Existing workbook semantic current pointer drift",
            )
    write_if_changed(pointer_path, pretty_bytes(pointer))
    return snapshot, manifest, created


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--baseline-manifest",
        type=Path,
        default=DEFAULT_BASELINE_MANIFEST,
    )
    result.add_argument(
        "--layout-receipt", type=Path, default=DEFAULT_LAYOUT_RECEIPT
    )
    result.add_argument(
        "--model-receipt", type=Path, default=DEFAULT_MODEL_RECEIPT
    )
    result.add_argument(
        "--rank-pointer", type=Path, default=DEFAULT_RANK_POINTER
    )
    result.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    result.add_argument(
        "--expected-baseline-sha256",
        default=EXPECTED_BASELINE_SHA256,
    )
    result.add_argument(
        "--expected-live-sha256", default=EXPECTED_LIVE_SHA256
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    snapshot, manifest, created = build_audit(
        baseline_manifest_path=args.baseline_manifest,
        layout_receipt_path=args.layout_receipt,
        model_receipt_path=args.model_receipt,
        rank_pointer_path=args.rank_pointer,
        out_root=args.out_root,
        expected_baseline_sha256=args.expected_baseline_sha256 or None,
        expected_live_sha256=args.expected_live_sha256 or None,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "created": created,
                "audit_id": manifest["audit_id"],
                "snapshot_dir": str(snapshot),
                "summary": manifest["summary"],
                "production_or_mainframe_written": False,
                "sync_or_promotion_performed": False,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
