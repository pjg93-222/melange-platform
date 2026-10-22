# -*- coding: utf-8 -*-
"""Extract the frozen workbook's audited legislative member/layout facts.

Python never interprets or calculates an XLSX through a spreadsheet library
in this workflow.  It verifies the immutable baseline receipt, consumes the
accepted @oai/artifact-tool inspection, and performs a bounded read-only
OOXML semantic comparison that ignores formula cached values only.

The legacy AG:AW efficiency block has 17 physical slots for 18 categories.
This extractor records only identities that are unambiguous in the workbook.
It never invents, approves, or silently completes the missing 18-to-17 policy.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal, InvalidOperation

import roster  # fec/ is on sys.path (bootstrap above); the one source of the roster size and member rows


SCHEMA = "legislation-workbook-audited-member-layout-v1"
HELPER_SCHEMA = "legislation-workbook-layout-artifact-extraction-v1"
BASELINE_SCHEMA = "legislation-production-baseline-v1"
WORKBOOK_BASENAME = "Moneyball Main Version - Blended.xlsx"
DEFAULT_LAYOUT_NAME = "audited_member_layout.json"
DEFAULT_RAW_NAME = "artifact_layout_extraction.json"
HELPER_TIMEOUT_ENV = "ARTIFACT_LAYOUT_HELPER_TIMEOUT_SECONDS"
DEFAULT_HELPER_TIMEOUT_SECONDS = 1800
CAPABILITY_SCHEMA = "accepted-legislation-workbook-producer-capability-v1"
CALIBRATION_PAYLOAD_SCHEMA = "accepted-legislation-workbook-payload-v1"
CALIBRATION_REPORT_SCHEMA = "accepted-legislation-artifact-tool-report-v1"
BIOGUIDE_RE = re.compile(r"^[A-Z]\d{6}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")
ERROR_TOKEN_RE = re.compile(r"#(?:REF!|DIV/0!|VALUE!|NAME\?|N/A|NUM!)")

CATEGORIES = (
    "Examination & Supervision",
    "CU Capital Requirements",
    "NCUA Budget & Insurance",
    "Interchange",
    "Payments",
    "Technology & Emerging",
    "Fraud Prevention",
    "Data Privacy & Cyber",
    "Charter Modernization",
    "Defense CU Issues",
    "Field of Membership",
    "CDFI Fund",
    "CFPB",
    "Credit Card Mandates",
    "TCPA",
    "Member Business Lending",
    "Mortgage & Housing",
    "CU Tax Status",
)

_RAW_LAST_ROW = roster.last_row("Legislative Score - Raw")
BLOCK_RANGES = {
    category: cell_range
    for category, cell_range in zip(
        CATEGORIES,
        (
            f"C3:J{_RAW_LAST_ROW}",
            f"K3:R{_RAW_LAST_ROW}",
            f"S3:Z{_RAW_LAST_ROW}",
            f"AA3:AH{_RAW_LAST_ROW}",
            f"AI3:AP{_RAW_LAST_ROW}",
            f"AQ3:AX{_RAW_LAST_ROW}",
            f"AY3:BF{_RAW_LAST_ROW}",
            f"BG3:BN{_RAW_LAST_ROW}",
            f"BO3:BV{_RAW_LAST_ROW}",
            f"BW3:CD{_RAW_LAST_ROW}",
            f"CE3:CL{_RAW_LAST_ROW}",
            f"CM3:CT{_RAW_LAST_ROW}",
            f"CU3:DB{_RAW_LAST_ROW}",
            f"DC3:DJ{_RAW_LAST_ROW}",
            f"DK3:DR{_RAW_LAST_ROW}",
            f"DS3:DZ{_RAW_LAST_ROW}",
            f"EA3:EH{_RAW_LAST_ROW}",
            f"EW3:FD{_RAW_LAST_ROW}",
        ),
    )
}

# This is the workbook's physical category layout.  P is the deliberately
# excluded non-legislative "CU-Bank Competitive Dynamics" slot.
CATEGORY_SUBLAYER_COLUMNS = {
    "Examination & Supervision": "C",
    "CU Capital Requirements": "D",
    "NCUA Budget & Insurance": "E",
    "Interchange": "F",
    "Payments": "G",
    "Technology & Emerging": "H",
    "Fraud Prevention": "I",
    "Data Privacy & Cyber": "J",
    "Charter Modernization": "K",
    "Defense CU Issues": "L",
    "Field of Membership": "M",
    "CU Tax Status": "N",
    "CDFI Fund": "O",
    "CFPB": "Q",
    "TCPA": "R",
    "Credit Card Mandates": "S",
    "Member Business Lending": "T",
    "Mortgage & Housing": "U",
}

EFFICIENCY_COLUMNS = (
    "AG",
    "AH",
    "AI",
    "AJ",
    "AK",
    "AL",
    "AM",
    "AN",
    "AO",
    "AP",
    "AQ",
    "AR",
    "AS",
    "AT",
    "AU",
    "AV",
    "AW",
)

AUDITED_SEMANTIC_SHEETS = (
    "Congestion",
    "Sub Layer - Raw",
    "Legislative Score - Normalized",
    "Legislative Score - Raw",
    "Press Release - Normalized",
    "Press Release - Raw",
)

CATEGORY_PRESS_COLUMNS = {
    "Examination & Supervision": "E",
    "CU Capital Requirements": "F",
    "NCUA Budget & Insurance": "G",
    "Interchange": "H",
    "Payments": "I",
    "Technology & Emerging": "J",
    "Fraud Prevention": "K",
    "Data Privacy & Cyber": "L",
    "Charter Modernization": "M",
    "Defense CU Issues": "N",
    "Field of Membership": "O",
    "CU Tax Status": "P",
    "CDFI Fund": "Q",
    "CFPB": "S",
    "Credit Card Mandates": "T",
    "TCPA": "U",
    "Member Business Lending": "V",
    "Mortgage & Housing": "W",
}

EFFICIENCY_CATEGORY_BY_COLUMN = {
    "AG": "Examination & Supervision",
    "AH": "CU Capital Requirements",
    "AI": "NCUA Budget & Insurance",
    "AJ": "Interchange",
    "AK": "Payments",
    "AL": "Technology & Emerging",
    "AM": "Fraud Prevention",
    "AN": "Data Privacy & Cyber",
    "AO": "Charter Modernization",
    "AP": "Defense CU Issues",
    "AQ": "Field of Membership",
    "AR": "CDFI Fund",
    "AS": "CFPB",
    "AT": "Credit Card Mandates",
    "AU": "TCPA",
    "AV": "Member Business Lending",
    "AW": "Mortgage & Housing",
}

EFFICIENCY_SOURCE_COLUMN_BY_COLUMN = {
    "AG": "H",
    "AH": "P",
    "AI": "X",
    "AJ": "AF",
    "AK": "AN",
    "AL": "AV",
    "AM": "BD",
    "AN": "BK",
    "AO": "BS",
    "AP": "CA",
    "AQ": "CI",
    "AR": "CQ",
    "AS": "CY",
    "AT": "DG",
    "AU": "DO",
    "AV": "DW",
    "AW": "EE",
}

EFFICIENCY_CATEGORY_TOKEN_BY_COLUMN = {
    "AG": "Examination & Supervision",
    "AH": "Credit Union Capital Requirements",
    "AI": "NCUA Budget & Share Insurance Fund",
    "AJ": "Interchange",
    "AK": "Payments",
    "AL": "Technology & Emerging Issues",
    "AM": "Fraud Prevention",
    "AN": "Data Privacy & Cybersecurity",
    "AO": "Charter Modernization & Governance",
    "AP": "Defense Credit Union Issues",
    "AQ": "Field of Membership",
    "AR": "CDFI Fund",
    "AS": "CFPB Restrain",
    "AT": "Credit Card & Consumer-Fee Mandates",
    "AU": "Telephone Consumer Protection Act Issues",
    "AV": "Member Business Lending",
    "AW": "Mortgage Lending & Housing",
}

EXPECTED_PRESS_MISMATCH_MEMBERS = {
    "André Carson",
    "Andrea Salinas",
    "Andrew Clyde",
    "Andrew Garbarino",
    "Andrew Ogles",
}

PRESS_NORMALIZED_FIRST_COLUMN = "E"
PRESS_NORMALIZED_LAST_COLUMN = "X"

CALIBRATION_MODEL_SHEETS = {
    "Co-Sponsor Interactions",
    "Original Co-Sponsor Interaction",
    "Sponsor Interactions",
    "Success Original Co-Sponsor",
    "Success Sponsor",
}
CALIBRATION_ARCHIVE_SHEET = "All Bills"


class LayoutExtractionError(RuntimeError):
    """The frozen input or artifact-tool extraction is incomplete/unsafe."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LayoutExtractionError(f"invalid JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LayoutExtractionError(f"JSON root must be an object: {path}")
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
        + b"\n"
    )
    os.replace(temporary, path)


def _manifest_path(value: Any, manifest: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        raise LayoutExtractionError("baseline workbook copy path is blank")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = manifest.parent / candidate
    return candidate.resolve()


def resolve_frozen_workbook(manifest_path: Path) -> dict[str, Any]:
    """Resolve and verify the one immutable frozen workbook copy."""
    manifest_path = manifest_path.resolve()
    manifest = read_object(manifest_path)
    if manifest.get("schema") != BASELINE_SCHEMA:
        raise LayoutExtractionError("frozen baseline manifest schema mismatch")
    if manifest.get("immutable_existing_copy_policy") is not True:
        raise LayoutExtractionError("baseline is not marked immutable")
    rows = [
        row
        for row in manifest.get("files") or []
        if isinstance(row, dict)
        and Path(str(row.get("copy") or "")).name == WORKBOOK_BASENAME
    ]
    if len(rows) != 1:
        raise LayoutExtractionError(
            "baseline must identify exactly one frozen blended workbook copy"
        )
    row = rows[0]
    copy_path = _manifest_path(row.get("copy"), manifest_path)
    source_path = _manifest_path(row.get("source"), manifest_path)
    baseline_root = manifest_path.parent.resolve()
    if copy_path == source_path:
        raise LayoutExtractionError("baseline copy resolves to the production workbook")
    if not copy_path.is_relative_to(baseline_root):
        raise LayoutExtractionError(
            "frozen workbook copy is outside the baseline directory"
        )
    if not copy_path.is_file():
        raise LayoutExtractionError(f"frozen workbook copy is missing: {copy_path}")
    expected_sha = str(row.get("sha256") or "").casefold()
    if not HEX_RE.fullmatch(expected_sha):
        raise LayoutExtractionError("baseline workbook SHA-256 is invalid")
    actual_sha = sha256_file(copy_path)
    if actual_sha != expected_sha:
        raise LayoutExtractionError(
            "frozen workbook copy SHA-256 differs from its baseline receipt"
        )
    expected_size = row.get("size")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size != copy_path.stat().st_size
    ):
        raise LayoutExtractionError(
            "frozen workbook copy size differs from its baseline receipt"
        )
    return {
        "manifest": manifest,
        "manifest_receipt": file_receipt(manifest_path),
        "workbook_path": copy_path,
        "workbook_receipt": file_receipt(copy_path),
        # This is manifest evidence only.  The production path is never opened.
        "production_source_declared": str(source_path),
    }


def _receipt_matches(path: Path, value: Mapping[str, Any]) -> bool:
    if not path.is_file():
        return False
    return (
        value.get("size") == path.stat().st_size
        and str(value.get("sha256") or "").casefold() == sha256_file(path)
    )


def resolve_zero_legislation_calibration(
    capability_path: Path,
    *,
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a zero-legislation artifact-tool calibration workbook.

    The verified accepted-workbook producer imports the exact frozen baseline
    and replaces only the five legislation relationship sheets.  This
    calibration fixture replaces them with headers only, causing the workbook
    engine to materialize downstream formula values with a zero legislative
    contribution.  The resulting Sub Layer values are therefore the preserved
    non-legislative residuals, without Python opening or calculating an XLSX.
    """
    capability_path = capability_path.resolve()
    capability = read_object(capability_path)
    producer = Path(__file__).with_name(
        "build_accepted_legislation_workbook.py"
    ).resolve()
    accepted_helper = (
        Path(__file__).resolve().parent.parent
        / "build_accepted_legislation_workbook.cjs"
    ).resolve()
    baseline_manifest = Path(
        str(baseline["manifest_receipt"]["path"])
    ).resolve()
    smoke = capability.get("smoke_test") or {}
    output_value = capability.get("output_workbook") or {}
    output_path = Path(str(output_value.get("path") or "")).resolve()
    manifest_value = capability.get("frozen_baseline_manifest") or {}
    if not (
        capability.get("schema") == CAPABILITY_SCHEMA
        and capability.get("status") == "PASS"
        and capability.get("approved_engine") == "@oai/artifact-tool"
        and capability.get("staged_only") is True
        and capability.get("production_written") is False
        and capability.get("baseline_input_sha256")
        == baseline["workbook_receipt"]["sha256"]
        and capability.get("producer_sha256") == sha256_file(producer)
        and capability.get("helper_sha256") == sha256_file(accepted_helper)
        and smoke.get("passed") is True
        and smoke.get("composite_rebind") is True
        and smoke.get("helper_source_unchanged") is True
        and smoke.get("required_sheets_present") is True
        and smoke.get("targeted_formula_error_count") == 0
        and (smoke.get("source_contract_tests") or {}).get("passed") is True
        and int(
            (smoke.get("source_contract_tests") or {}).get("tests_run") or 0
        )
        > 0
        and _receipt_matches(baseline_manifest, manifest_value)
        and _receipt_matches(output_path, output_value)
    ):
        raise LayoutExtractionError(
            "zero-legislation calibration capability is stale or unverified"
        )

    payload_path = output_path.parent / "artifact_tool_payload.json"
    report_path = output_path.parent / "inspection.json"
    payload = read_object(payload_path)
    report = read_object(report_path)
    declared_model = Path(
        str((payload.get("baseline") or {}).get("model") or "")
    ).resolve()
    declared_output = Path(
        str((payload.get("outputs") or {}).get("model") or "")
    ).resolve()
    model_sheets = payload.get("model_sheets")
    if not (
        payload.get("schema") == CALIBRATION_PAYLOAD_SCHEMA
        and payload.get("production_written") is False
        and payload.get("generation_id") == "0" * 64
        and declared_model == baseline["workbook_path"]
        and declared_output == output_path
        and isinstance(model_sheets, dict)
        and set(model_sheets) == CALIBRATION_MODEL_SHEETS
        and all(
            isinstance(rows, list)
            and len(rows) == 1
            and isinstance(rows[0], list)
            and len(rows[0]) == 13
            for rows in model_sheets.values()
        )
    ):
        raise LayoutExtractionError(
            "calibration payload is not the exact zero-legislation fixture"
        )

    inspection_value = capability.get("inspection") or {}
    preview_values = capability.get("previews")
    preview_receipts: list[dict[str, Any]] = []
    if not isinstance(preview_values, list) or len(preview_values) < 3:
        raise LayoutExtractionError(
            "calibration capability lacks artifact-tool render evidence"
        )
    for item in preview_values:
        if not isinstance(item, dict):
            raise LayoutExtractionError("calibration preview receipt is invalid")
        preview_path = Path(str(item.get("path") or "")).resolve()
        if not _receipt_matches(preview_path, item):
            raise LayoutExtractionError(
                f"calibration preview receipt is stale: {preview_path}"
            )
        preview_receipts.append(
            {
                **file_receipt(preview_path),
                "sheet": preview_path.stem,
                "range": "verified calibration mutation preview",
                "file": preview_path.name,
            }
        )
    model_mutations = [
        row
        for row in report.get("mutations") or []
        if isinstance(row, dict) and row.get("workbook") == "model"
    ]
    archive_mutations = [
        row
        for row in report.get("mutations") or []
        if isinstance(row, dict) and row.get("workbook") == "archive"
    ]
    if not (
        _receipt_matches(report_path, inspection_value)
        and report.get("schema") == CALIBRATION_REPORT_SCHEMA
        and report.get("status") == "PASS"
        and report.get("engine") == "@oai/artifact-tool"
        and report.get("staged_only") is True
        and report.get("production_written") is False
        and report.get("generation_id") == "0" * 64
        and report.get("required_sheets_present") is True
        and report.get("targeted_formula_error_count") == 0
        and set(row.get("sheet") for row in model_mutations)
        == CALIBRATION_MODEL_SHEETS
        and len(model_mutations) == len(CALIBRATION_MODEL_SHEETS)
        and all(
            row.get("address") == "A1:M1"
            and row.get("rows") == 1
            and row.get("cols") == 13
            and row.get("actual_error_token_count") == 0
            for row in model_mutations
        )
        and len(archive_mutations) == 1
        and archive_mutations[0].get("sheet") == CALIBRATION_ARCHIVE_SHEET
        and Path(
            str((report.get("outputs") or {}).get("model") or "")
        ).resolve()
        == output_path
    ):
        raise LayoutExtractionError(
            "calibration inspection does not prove the exact bounded mutations"
        )
    return {
        "capability": capability,
        "capability_receipt": file_receipt(capability_path),
        "workbook_path": output_path,
        "workbook_receipt": file_receipt(output_path),
        "payload_receipt": file_receipt(payload_path),
        "inspection_receipt": file_receipt(report_path),
        "producer_receipt": file_receipt(producer),
        "accepted_helper_receipt": file_receipt(accepted_helper),
        "preview_receipts": preview_receipts,
        "proof": {
            "generation_id": "0" * 64,
            "legislation_relationship_rows": 0,
            "model_mutations": sorted(CALIBRATION_MODEL_SHEETS),
            "mutation_range_each": "A1:M1",
            "nonlegislative_residual_rule": (
                "Sub Layer value minus zero legislative contribution"
            ),
        },
    }


def _column_number(column: str) -> int:
    value = 0
    for character in column:
        value = value * 26 + ord(character) - 64
    return value


def _column_name(number: int) -> str:
    value = number
    result = ""
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


OOXML_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OOXML_DOCUMENT_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
OOXML_PACKAGE_REL_NS = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)


def _cell_sort_key(address: str) -> tuple[int, int]:
    match = CELL_RE.fullmatch(address)
    if match is None:
        raise LayoutExtractionError(f"invalid OOXML cell address: {address}")
    column, row = match.groups()
    return int(row), _column_number(column)


def _normalize_ooxml_number(value: str) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise LayoutExtractionError(
            f"invalid OOXML numeric cell value: {value!r}"
        ) from exc
    if not number.is_finite():
        raise LayoutExtractionError(
            f"non-finite OOXML numeric cell value: {value!r}"
        )
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def _xml_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext())


def _read_shared_strings(package: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in package.namelist():
        return []
    try:
        root = ET.fromstring(package.read(name))
    except (ET.ParseError, KeyError) as exc:
        raise LayoutExtractionError(
            "invalid OOXML shared-string table"
        ) from exc
    return [
        "".join(
            node.text or ""
            for node in item.iter(f"{{{OOXML_MAIN_NS}}}t")
        )
        for item in root.findall(f"{{{OOXML_MAIN_NS}}}si")
    ]


def _ooxml_sheet_targets(package: zipfile.ZipFile) -> dict[str, str]:
    try:
        workbook = ET.fromstring(package.read("xl/workbook.xml"))
        relationships = ET.fromstring(
            package.read("xl/_rels/workbook.xml.rels")
        )
    except (ET.ParseError, KeyError) as exc:
        raise LayoutExtractionError(
            "XLSX lacks a valid workbook/relationship package"
        ) from exc
    rel_targets = {
        str(row.get("Id") or ""): str(row.get("Target") or "")
        for row in relationships.findall(
            f"{{{OOXML_PACKAGE_REL_NS}}}Relationship"
        )
    }
    result: dict[str, str] = {}
    for sheet in workbook.findall(
        f".//{{{OOXML_MAIN_NS}}}sheet"
    ):
        name = str(sheet.get("name") or "")
        rel_id = str(
            sheet.get(f"{{{OOXML_DOCUMENT_REL_NS}}}id") or ""
        )
        target = rel_targets.get(rel_id, "")
        if not name or not target:
            raise LayoutExtractionError(
                "workbook sheet lacks a resolvable OOXML relationship"
            )
        if target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(posixpath.join("xl", target))
        if resolved.startswith("../") or resolved not in package.namelist():
            raise LayoutExtractionError(
                f"workbook sheet target is unsafe/missing: {name}"
            )
        result[name] = resolved
    return result


def _canonical_ooxml_cell(
    cell: ET.Element,
    *,
    shared_strings: Sequence[str],
) -> dict[str, Any] | None:
    address = str(cell.get("r") or "").upper()
    _cell_sort_key(address)
    formula = cell.find(f"{{{OOXML_MAIN_NS}}}f")
    if formula is not None:
        # Formula cached values are deliberately excluded.  Formula text and
        # formula attributes remain part of the semantic identity.
        return {
            "address": address,
            "kind": "formula",
            "formula": formula.text or "",
            "formula_attributes": sorted(
                (str(key), str(value))
                for key, value in formula.attrib.items()
            ),
        }

    value_node = cell.find(f"{{{OOXML_MAIN_NS}}}v")
    inline_node = cell.find(f"{{{OOXML_MAIN_NS}}}is")
    cell_type = str(cell.get("t") or "n")
    if value_node is None and inline_node is None:
        # Style-only/empty physical cells do not carry workbook semantics.
        return None
    raw_value = (
        _xml_text(inline_node)
        if inline_node is not None
        else (value_node.text or "")
    )
    if cell_type == "s":
        try:
            text_value = shared_strings[int(raw_value)]
        except (ValueError, IndexError) as exc:
            raise LayoutExtractionError(
                f"invalid OOXML shared-string index at {address}"
            ) from exc
        value_type = "text"
        canonical_value: Any = text_value
    elif cell_type in {"inlineStr", "str"}:
        value_type = "text"
        canonical_value = raw_value
    elif cell_type == "b":
        if raw_value not in {"0", "1"}:
            raise LayoutExtractionError(
                f"invalid OOXML boolean at {address}"
            )
        value_type = "boolean"
        canonical_value = raw_value == "1"
    elif cell_type == "e":
        value_type = "error"
        canonical_value = raw_value
    elif cell_type == "d":
        value_type = "date"
        canonical_value = raw_value
    else:
        value_type = "number"
        canonical_value = _normalize_ooxml_number(raw_value)
    return {
        "address": address,
        "kind": "value",
        "value_type": value_type,
        "value": canonical_value,
    }


def canonical_ooxml_sheet_semantics(
    workbook_path: Path,
    *,
    sheets: Sequence[str] = AUDITED_SEMANTIC_SHEETS,
) -> dict[str, dict[str, Any]]:
    """Hash semantic cells without considering formula cached values.

    This is a bounded, read-only OOXML package audit.  It is not a calculation
    engine and does not use a Python spreadsheet library.
    """
    workbook_path = workbook_path.resolve()
    try:
        package = zipfile.ZipFile(workbook_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise LayoutExtractionError(
            f"invalid XLSX OOXML package: {workbook_path}"
        ) from exc
    with package:
        shared_strings = _read_shared_strings(package)
        targets = _ooxml_sheet_targets(package)
        if any(sheet not in targets for sheet in sheets):
            missing = sorted(set(sheets) - set(targets))
            raise LayoutExtractionError(
                f"OOXML workbook lacks audited sheets: {missing}"
            )
        result: dict[str, dict[str, Any]] = {}
        for sheet in sheets:
            try:
                root = ET.fromstring(package.read(targets[sheet]))
            except (ET.ParseError, KeyError) as exc:
                raise LayoutExtractionError(
                    f"invalid OOXML worksheet: {sheet}"
                ) from exc
            cells = []
            for cell in root.findall(
                f".//{{{OOXML_MAIN_NS}}}sheetData/"
                f"{{{OOXML_MAIN_NS}}}row/"
                f"{{{OOXML_MAIN_NS}}}c"
            ):
                value = _canonical_ooxml_cell(
                    cell, shared_strings=shared_strings
                )
                if value is not None:
                    cells.append(value)
            cells.sort(key=lambda row: _cell_sort_key(row["address"]))
            formula_count = sum(
                row["kind"] == "formula" for row in cells
            )
            digest = hashlib.sha256(canonical_bytes(cells)).hexdigest()
            result[sheet] = {
                "sha256": digest,
                "semantic_cell_count": len(cells),
                "formula_count": formula_count,
                "nonformula_value_count": len(cells) - formula_count,
            }
        return result


def verify_six_sheet_semantic_equality(
    baseline_path: Path,
    calibration_path: Path,
) -> dict[str, Any]:
    baseline = canonical_ooxml_sheet_semantics(baseline_path)
    calibration = canonical_ooxml_sheet_semantics(calibration_path)
    mismatches = [
        sheet
        for sheet in AUDITED_SEMANTIC_SHEETS
        if baseline[sheet] != calibration[sheet]
    ]
    if mismatches:
        raise LayoutExtractionError(
            "zero-legislation calibration is not canonically identical to "
            f"the frozen baseline on audited sheets: {mismatches}"
        )
    return {
        "status": "PASS",
        "sheet_count": len(AUDITED_SEMANTIC_SHEETS),
        "sheets": {
            sheet: {
                "baseline": baseline[sheet],
                "calibration": calibration[sheet],
                "equal": True,
            }
            for sheet in AUDITED_SEMANTIC_SHEETS
        },
        "canonical_rule": (
            "cell address + exact formula/formula attributes or canonical "
            "nonformula value; formula cached values only are ignored"
        ),
        "ignored": ["formula_cached_values"],
    }


def _table_shape(
    record: Mapping[str, Any],
    *,
    sheet: str,
    address: str,
    rows: int,
    columns: int,
) -> list[list[Any]]:
    values = record.get("values")
    if not (
        record.get("kind") == "table"
        and record.get("sheet") == sheet
        and record.get("address") == address
        and record.get("rows") == rows
        and record.get("cols") == columns
        and isinstance(values, list)
        and len(values) == rows
        and all(isinstance(row, list) and len(row) == columns for row in values)
    ):
        raise LayoutExtractionError(
            f"full artifact inspection table is incomplete: {sheet}!{address}"
        )
    return values


def _expected_press_normalization_formula(header: str) -> str:
    return (
        "=_xlfn.LET(\n"
        f"_xlpm.x,N(Table10[[#This Row],[{header}]]),\n"
        f"_xlpm.posMax,MAX(0,MAX(Table10[{header}])),\n"
        f"_xlpm.negMax,ABS(MIN(0,MIN(Table10[{header}]))),\n"
        "IF(_xlpm.x=0,0,\n"
        "IF(_xlpm.x>0,\n"
        "IF(_xlpm.posMax=0,0,_xlpm.x/_xlpm.posMax),\n"
        "IF(_xlpm.negMax=0,0,_xlpm.x/_xlpm.negMax)\n"
        "))\n"
        ")"
    )


def _expected_sublayer_formula(
    *,
    legislative_first: str,
    legislative_last: str,
    press_column: str,
    sublayer_row: int,
) -> str:
    legislative_row = sublayer_row - 1
    press_row = sublayer_row - 2
    return (
        "=SUMPRODUCT("
        f"'Legislative Score - Normalized'!"
        f"{legislative_first}{legislative_row}:"
        f"{legislative_last}{legislative_row},"
        f"'Legislative Score - Normalized'!"
        f"{legislative_first}$2:{legislative_last}$2"
        ") + SUMPRODUCT("
        f"'Press Release - Normalized'!{press_column}{press_row},"
        f"'Press Release - Normalized'!{press_column}"
        f"${roster.footer_row('Press Release - Normalized')})"
    )


def _excel_n(value: Any, *, label: str) -> float:
    if value is None or isinstance(value, str):
        return 0.0
    if isinstance(value, bool):
        return float(value)
    return finite_number(value, label=label)


def _semantic_table_errors(
    record: Mapping[str, Any],
) -> list[dict[str, Any]]:
    values = record.get("values")
    if not isinstance(values, list):
        return []
    errors: list[dict[str, Any]] = []
    for row_index, row in enumerate(values, start=1):
        if not isinstance(row, list):
            continue
        for column_index, value in enumerate(row, start=1):
            if isinstance(value, str) and ERROR_TOKEN_RE.search(value):
                errors.append(
                    {
                        "sheet": str(record.get("sheet") or ""),
                        "address": f"{_column_name(column_index)}{row_index}",
                        "value": value,
                    }
                )
    return errors


def helper_payload_from_full_artifact_inspection(
    sidecar_path: Path,
    *,
    calibration: Mapping[str, Any],
    baseline_workbook_path: Path,
) -> dict[str, Any]:
    """Build audited facts from a hash-bound artifact inspection + OOXML.

    The sidecar's cached formula errors are diagnostic.  All numbers required
    by the numeric plan are reconstructed from nonformula Press Release - Raw
    values, exact Press Release - Normalized LET formulas, exact literal row
    references in Sub Layer - Raw, and workbook weights.
    """
    sidecar_path = sidecar_path.resolve()
    expected_sidecar = Path(
        str(calibration["workbook_path"]) + ".inspect.ndjson"
    ).resolve()
    if sidecar_path != expected_sidecar or not sidecar_path.is_file():
        raise LayoutExtractionError(
            "full artifact inspection must be adjacent to its exact workbook"
        )
    semantic_equality = verify_six_sheet_semantic_equality(
        baseline_workbook_path,
        calibration["workbook_path"],
    )
    n_members = roster.count()
    raw_last = roster.last_row("Legislative Score - Raw")
    sublayer_last = roster.last_row("Sub Layer - Raw")
    congestion_last = roster.last_row("Congestion")
    press_footer = roster.footer_row("Press Release - Normalized")
    press_normalized_last = press_footer + 2  # footer + the two rows under it
    press_raw_last = roster.footer_row("Press Release - Raw")
    target_addresses = {
        "Legislative Score - Raw": f"A1:FD{raw_last}",
        "Legislative Score - Normalized": f"A1:FD{raw_last}",
        "Sub Layer - Raw": f"A1:BB{sublayer_last}",
        "Congestion": f"A1:Q{congestion_last}",
        "Press Release - Normalized": f"A1:X{press_normalized_last}",
        "Press Release - Raw": f"A1:Z{press_raw_last}",
    }
    targets = set(target_addresses.items())
    tables: dict[tuple[str, str], dict[str, Any]] = {}
    target_formulas: dict[tuple[str, str], str] = {}
    all_formula_cells: set[tuple[str, str]] = set()
    observed_formula_errors: list[dict[str, Any]] = []
    workbook_header: dict[str, Any] | None = None
    observed_table_count = 0
    with sidecar_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LayoutExtractionError(
                    f"invalid artifact inspection NDJSON line {line_number}"
                ) from exc
            if not isinstance(row, dict):
                continue
            kind = row.get("kind")
            if kind == "workbook":
                if workbook_header is not None:
                    raise LayoutExtractionError(
                        "artifact inspection has multiple workbook headers"
                    )
                workbook_header = row
            sheet = str(row.get("sheet") or "")
            address = str(row.get("address") or "").upper()
            key = (sheet, address)
            if kind == "table":
                observed_table_count += 1
                observed_formula_errors.extend(_semantic_table_errors(row))
                if key in targets:
                    if key in tables:
                        raise LayoutExtractionError(
                            f"duplicate artifact inspection table: {key}"
                        )
                    tables[key] = row
            if kind == "formula" and isinstance(row.get("formula"), str):
                if not CELL_RE.fullmatch(address):
                    continue
                if key in all_formula_cells:
                    raise LayoutExtractionError(
                        f"duplicate artifact formula: {sheet}!{address}"
                    )
                all_formula_cells.add(key)
                if sheet in target_addresses:
                    target_formulas[key] = row["formula"].replace(
                        "\r\n", "\n"
                    )
    if not (
        workbook_header
        and workbook_header.get("sheets") == 30
        and workbook_header.get("tables") == 30
        and observed_table_count == 30
        and set(tables) == targets
    ):
        raise LayoutExtractionError(
            "full artifact inspection lacks its exact workbook/table closure"
        )

    raw = _table_shape(
        tables[("Legislative Score - Raw", f"A1:FD{raw_last}")],
        sheet="Legislative Score - Raw",
        address=f"A1:FD{raw_last}",
        rows=raw_last,
        columns=160,
    )
    normalized = _table_shape(
        tables[("Legislative Score - Normalized", f"A1:FD{raw_last}")],
        sheet="Legislative Score - Normalized",
        address=f"A1:FD{raw_last}",
        rows=raw_last,
        columns=160,
    )
    sublayer = _table_shape(
        tables[("Sub Layer - Raw", f"A1:BB{sublayer_last}")],
        sheet="Sub Layer - Raw",
        address=f"A1:BB{sublayer_last}",
        rows=sublayer_last,
        columns=54,
    )
    congestion = _table_shape(
        tables[("Congestion", f"A1:Q{congestion_last}")],
        sheet="Congestion",
        address=f"A1:Q{congestion_last}",
        rows=congestion_last,
        columns=17,
    )
    press_normalized = _table_shape(
        tables[("Press Release - Normalized", f"A1:X{press_normalized_last}")],
        sheet="Press Release - Normalized",
        address=f"A1:X{press_normalized_last}",
        rows=press_normalized_last,
        columns=24,
    )
    press_raw = _table_shape(
        tables[("Press Release - Raw", f"A1:Z{press_raw_last}")],
        sheet="Press Release - Raw",
        address=f"A1:Z{press_raw_last}",
        rows=press_raw_last,
        columns=26,
    )

    congestion_by_name: dict[str, tuple[str, int]] = {}
    for offset, row in enumerate(congestion[2:], start=3):
        name = " ".join(str(row[0] or "").split())
        bioguide = str(row[1] or "").strip()
        if (
            not name
            or name in congestion_by_name
            or not BIOGUIDE_RE.fullmatch(bioguide)
        ):
            raise LayoutExtractionError(
                f"invalid/duplicate Congestion member at row {offset}"
            )
        congestion_by_name[name] = (bioguide, offset)
    if len(congestion_by_name) != n_members or len(
        {value[0] for value in congestion_by_name.values()}
    ) != n_members:
        raise LayoutExtractionError(
            f"Congestion must contain {n_members} unique members and Bioguides"
        )
    members = []
    for index in range(n_members):
        raw_name = " ".join(str(raw[index + 2][0] or "").split())
        sublayer_name = " ".join(str(sublayer[index + 3][1] or "").split())
        if not raw_name or raw_name != sublayer_name:
            raise LayoutExtractionError(
                f"inspection member mismatch at index {index}"
            )
        if raw_name not in congestion_by_name:
            raise LayoutExtractionError(
                f"inspection member absent from Congestion: {raw_name}"
            )
        bioguide, congestion_row = congestion_by_name[raw_name]
        members.append(
            {
                "name": raw_name,
                "bioguide": bioguide,
                "bioguide_id": bioguide,
                "raw_row": index + 3,
                "sublayer_row": index + 4,
                "congestion_row": congestion_row,
            }
        )

    press_formula_mismatches: list[dict[str, Any]] = []
    press_first = _column_number(PRESS_NORMALIZED_FIRST_COLUMN)
    press_last = _column_number(PRESS_NORMALIZED_LAST_COLUMN)
    if press_last - press_first + 1 != 20:
        raise LayoutExtractionError("Press Normalized formula block is not 20 columns")
    for column_number in range(press_first, press_last + 1):
        column = _column_name(column_number)
        header = str(press_raw[0][column_number - 1] or "")
        if (
            not header
            or str(press_normalized[0][column_number - 1] or "") != header
        ):
            raise LayoutExtractionError(
                f"Press Raw/Normalized header mismatch at {column}"
            )
        expected = _expected_press_normalization_formula(header)
        for row_number in roster.rows("Press Release - Normalized"):
            formula = target_formulas.get(
                ("Press Release - Normalized", f"{column}{row_number}"),
                "",
            )
            if formula != expected:
                press_formula_mismatches.append(
                    {
                        "cell": f"{column}{row_number}",
                        "header": header,
                        "formula": formula,
                    }
                )
    press_formula_cells = {
        address
        for (sheet, address) in target_formulas
        if sheet == "Press Release - Normalized"
        and (match := CELL_RE.fullmatch(address))
        and press_first <= _column_number(match.group(1)) <= press_last
        and 2 <= int(match.group(2)) <= roster.last_row("Press Release - Normalized")
    }
    if (
        len(press_formula_cells) != n_members * 20
        or press_formula_mismatches
    ):
        raise LayoutExtractionError(
            f"all {n_members * 20:,} Press Normalized LET formulas were not exact"
        )

    press_order_mismatches: list[dict[str, Any]] = []
    for index in range(n_members):
        legislative_name = members[index]["name"]
        press_name = " ".join(str(press_raw[index + 1][0] or "").split())
        normalized_name = " ".join(
            str(press_normalized[index + 1][0] or "").split()
        )
        if not press_name or press_name != normalized_name:
            raise LayoutExtractionError(
                f"Press Raw/Normalized member mismatch at literal row {index + 2}"
            )
        if legislative_name != press_name:
            press_order_mismatches.append(
                {
                    "member_index": index,
                    "sublayer_row": index + 4,
                    "press_row": index + 2,
                    "legislative_member": legislative_name,
                    "press_literal_member": press_name,
                }
            )
    if not (
        len(press_order_mismatches) == 5
        and {
            row["legislative_member"] for row in press_order_mismatches
        }
        == EXPECTED_PRESS_MISMATCH_MEMBERS
        and {
            row["press_literal_member"] for row in press_order_mismatches
        }
        == EXPECTED_PRESS_MISMATCH_MEMBERS
    ):
        raise LayoutExtractionError(
            "expected five-member literal press-row mismatch was not exact"
        )

    legislative_weights: dict[str, list[float]] = {}
    nonlegislative: dict[str, list[float]] = {}
    formula_mismatches: list[dict[str, Any]] = []
    for category in CATEGORIES:
        first = re.match(r"^([A-Z]+)", BLOCK_RANGES[category])
        if first is None:
            raise LayoutExtractionError(f"invalid raw block: {category}")
        first_column = first.group(1)
        first_number = _column_number(first_column)
        last_column = _column_name(first_number + 4)
        legislative_weights[category] = [
            finite_number(
                normalized[1][first_number - 1 + offset],
                label=f"{category} calibration weight {offset}",
            )
            for offset in range(5)
        ]
        press_column = CATEGORY_PRESS_COLUMNS[category]
        press_index = _column_number(press_column) - 1
        raw_press_values = [
            _excel_n(
                press_raw[index + 1][press_index],
                label=f"{category} Press Raw row {index + 2}",
            )
            for index in range(n_members)
        ]
        positive_max = max(0.0, max(raw_press_values))
        negative_max = abs(min(0.0, min(raw_press_values)))
        press_weight = finite_number(
            press_normalized[press_footer - 1][press_index],
            label=f"{category} Press Normalized weight row {press_footer}",
        )
        residuals = []
        for index, raw_press_value in enumerate(raw_press_values):
            if raw_press_value == 0:
                normalized_press = 0.0
            elif raw_press_value > 0:
                normalized_press = (
                    0.0
                    if positive_max == 0
                    else raw_press_value / positive_max
                )
            else:
                normalized_press = (
                    0.0
                    if negative_max == 0
                    else raw_press_value / negative_max
                )
            residual = normalized_press * press_weight
            residuals.append(0.0 if abs(residual) < 1e-12 else residual)
            sublayer_row = index + 4
            cell = f"{CATEGORY_SUBLAYER_COLUMNS[category]}{sublayer_row}"
            formula = target_formulas.get(("Sub Layer - Raw", cell), "")
            expected = _expected_sublayer_formula(
                legislative_first=first_column,
                legislative_last=last_column,
                press_column=press_column,
                sublayer_row=sublayer_row,
            )
            if formula != expected:
                formula_mismatches.append(
                    {
                        "category": category,
                        "cell": cell,
                        "formula": formula,
                        "expected": expected,
                    }
                )
        nonlegislative[category] = residuals
    if formula_mismatches:
        raise LayoutExtractionError(
            f"all {n_members * 18:,} category Sub Layer formulas were not exact"
        )

    efficiency_slots = []
    efficiency_mismatches: list[dict[str, Any]] = []
    for column in EFFICIENCY_COLUMNS:
        category = EFFICIENCY_CATEGORY_BY_COLUMN[column]
        source_column = EFFICIENCY_SOURCE_COLUMN_BY_COLUMN[column]
        token = EFFICIENCY_CATEGORY_TOKEN_BY_COLUMN[column]
        anchor = target_formulas.get(("Sub Layer - Raw", f"{column}4"), "")
        source_anchor = (
            f"'Legislative Score - Normalized'!{source_column}3"
        )
        if token not in anchor or anchor.count(source_anchor) != 1:
            efficiency_mismatches.append(
                {"cell": f"{column}4", "formula": anchor}
            )
        slot_formulas = []
        for row_number in roster.rows("Sub Layer - Raw"):
            formula = target_formulas.get(
                ("Sub Layer - Raw", f"{column}{row_number}"), ""
            )
            expected = anchor.replace(
                source_anchor,
                f"'Legislative Score - Normalized'!"
                f"{source_column}{row_number - 1}",
                1,
            )
            if not anchor or formula != expected:
                efficiency_mismatches.append(
                    {
                        "cell": f"{column}{row_number}",
                        "formula": formula,
                        "expected": expected,
                    }
                )
            slot_formulas.append(formula)
        efficiency_slots.append(
            {
                "column": column,
                "status": "IDENTIFIED_FROM_WORKBOOK",
                "category_candidates": [category],
                "evidence": {
                    "formula_count": len(slot_formulas),
                    "formula_sha256": hashlib.sha256(
                        canonical_bytes(slot_formulas)
                    ).hexdigest(),
                    "source_success_column": source_column,
                    "category_formula_token": token,
                    "literal_formula_rows_verified": True,
                },
            }
        )
    if efficiency_mismatches:
        raise LayoutExtractionError(
            f"all {n_members * 17:,} AG:AW efficiency formulas were not exact"
        )

    diagnostic_errors = []
    blocking_errors = []
    for error in observed_formula_errors:
        key = (error["sheet"], error["address"])
        if key in all_formula_cells:
            diagnostic_errors.append(error)
        else:
            blocking_errors.append(error)
    sidecar_receipt = file_receipt(sidecar_path)
    return {
        "schema": HELPER_SCHEMA,
        "status": "PASS" if not blocking_errors else "BLOCKED",
        "engine": "@oai/artifact-tool",
        "extraction_basis": "full_artifact_inspection_plus_ooxml",
        "production_written": False,
        "source_workbook_sha256": calibration["workbook_receipt"]["sha256"],
        "source_workbook_sha256_after": calibration["workbook_receipt"]["sha256"],
        "raw_block_ranges": BLOCK_RANGES,
        "sublayer_category_columns": CATEGORY_SUBLAYER_COLUMNS,
        "members": members,
        "sublayer_nonlegislative_values": nonlegislative,
        "sublayer_legislative_weights": legislative_weights,
        "efficiency_slots": efficiency_slots,
        "sublayer_formula_audit": {
            "checked_cells": n_members * 18,
            "mismatch_count": 0,
            "mismatches": [],
            "strict_exact_formula_comparison": True,
        },
        "press_normalized_formula_audit": {
            "checked_cells": n_members * 20,
            "mismatch_count": 0,
            "mismatches": [],
            "strict_exact_let_formula_comparison": True,
        },
        "press_literal_row_alignment": {
            "status": "REQUIRES_EXPLICIT_POLICY_DECISION",
            "mismatch_count": len(press_order_mismatches),
            "mismatches": press_order_mismatches,
            "reconstruction_semantics": "preserve_literal_formula_row_reference",
            "member_join_applied": False,
        },
        "efficiency_mapping_evidence": {
            "status": "UNRESOLVED_MISSING_CU_TAX",
            "identified_slot_count": 17,
            "omitted_categories": ["CU Tax Status"],
            "inferred_merge": False,
        },
        "formula_error_scan": {
            "status": "PASS" if not blocking_errors else "BLOCKED",
            "match_count": len(blocking_errors),
            "matches": blocking_errors[:100],
            "formula_cache_diagnostic_count": len(diagnostic_errors),
            "formula_cache_diagnostics": diagnostic_errors[:100],
            "formula_cache_errors_are_nonblocking": True,
            "global_table_count": observed_table_count,
            "audited_tables": [
                f"{sheet}!{address}" for sheet, address in sorted(targets)
            ],
        },
        "semantic_equality": semantic_equality,
        "workbook_inspections": [
            {
                **sidecar_receipt,
                "workbook_header": workbook_header,
                "table_count": len(tables),
                "global_table_count": observed_table_count,
                "target_formula_count": len(target_formulas),
                "all_formula_cell_count": len(all_formula_cells),
            }
        ],
        "renders": calibration["preview_receipts"],
    }


def helper_geometry() -> dict[str, Any]:
    """The member count and member rows the artifact helper derives every range from; it has no built-in roster size."""
    return {
        "member_count": roster.count(),
        "sheets": {
            sheet: {
                "first_row": roster.first_row(sheet),
                "last_row": roster.last_row(sheet),
            }
            for sheet in (
                "Legislative Score - Raw",
                "Legislative Score - Normalized",
                "Sub Layer - Raw",
                "Congestion",
            )
        },
    }


def artifact_runtime(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = dict(os.environ if environ is None else environ)
    node = Path(str(values.get("ARTIFACT_TOOL_NODE") or "")).resolve()
    modules = Path(
        str(values.get("ARTIFACT_TOOL_NODE_MODULES") or "")
    ).resolve()
    if not node.is_file():
        raise LayoutExtractionError(
            "ARTIFACT_TOOL_NODE must name the loader-provided Node executable"
        )
    if not modules.is_dir():
        raise LayoutExtractionError(
            "ARTIFACT_TOOL_NODE_MODULES must name the loader-provided module directory"
        )
    child = dict(values)
    existing = str(child.get("NODE_PATH") or "").strip()
    child["NODE_PATH"] = (
        str(modules) if not existing else str(modules) + os.pathsep + existing
    )
    return {"node": node, "modules": modules, "environment": child}


def finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LayoutExtractionError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise LayoutExtractionError(f"{label} must be finite")
    return number


def _validate_members(rows: Any) -> list[dict[str, Any]]:
    n_members = roster.count()
    if not isinstance(rows, list) or len(rows) != n_members:
        raise LayoutExtractionError(
            f"extraction must contain exactly {n_members} members"
        )
    names: set[str] = set()
    bioguides: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise LayoutExtractionError(f"member {index} is not an object")
        name = " ".join(str(raw.get("name") or "").split())
        bioguide = str(
            raw.get("bioguide_id") or raw.get("bioguide") or ""
        ).strip()
        expected_raw_row = index + 3
        expected_sublayer_row = index + 4
        if not name or name in names:
            raise LayoutExtractionError(f"member {index} name is blank/duplicate")
        if not BIOGUIDE_RE.fullmatch(bioguide) or bioguide in bioguides:
            raise LayoutExtractionError(
                f"member {index} Bioguide is invalid/duplicate"
            )
        if raw.get("raw_row") != expected_raw_row:
            raise LayoutExtractionError(f"member {index} raw row is noncanonical")
        if raw.get("sublayer_row") != expected_sublayer_row:
            raise LayoutExtractionError(
                f"member {index} sublayer row is noncanonical"
            )
        if raw.get("congestion_row") is None:
            raise LayoutExtractionError(
                f"member {index} lacks its audited Congestion row"
            )
        names.add(name)
        bioguides.add(bioguide)
        result.append(
            {
                "name": name,
                "bioguide": bioguide,
                "bioguide_id": bioguide,
                "raw_row": expected_raw_row,
                "sublayer_row": expected_sublayer_row,
            }
        )
    return result


def _validate_efficiency_slots(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) != 17:
        raise LayoutExtractionError("efficiency facts must cover all 17 AG:AW slots")
    result: list[dict[str, Any]] = []
    for column, raw in zip(EFFICIENCY_COLUMNS, rows):
        if not isinstance(raw, dict) or raw.get("column") != column:
            raise LayoutExtractionError(
                f"efficiency facts are missing/reordered at {column}"
            )
        status = raw.get("status")
        candidates = raw.get("category_candidates")
        evidence = raw.get("evidence")
        if (
            status not in {"UNRESOLVED", "IDENTIFIED_FROM_WORKBOOK"}
            or not isinstance(candidates, list)
            or any(candidate not in CATEGORIES for candidate in candidates)
            or not isinstance(evidence, dict)
        ):
            raise LayoutExtractionError(f"efficiency slot {column} facts invalid")
        if status == "IDENTIFIED_FROM_WORKBOOK":
            if len(candidates) != 1:
                raise LayoutExtractionError(
                    f"identified efficiency slot {column} is not unique"
                )
            categories = list(candidates)
            aggregation = "identity"
        else:
            categories = []
            aggregation = None
        # These fields match the numeric-plan interface, but unresolved slots
        # deliberately cannot pass its approval gate.
        result.append(
            {
                "column": column,
                "status": status,
                "categories": categories,
                "aggregation": aggregation,
                "category_candidates": list(candidates),
                "evidence": evidence,
                "approved_by_user": False,
            }
        )
    return result


def validate_helper_payload(
    payload: Mapping[str, Any], *, expected_sha256: str
) -> dict[str, Any]:
    """Validate a complete artifact-tool extraction and normalize its values."""
    if not (
        payload.get("schema") == HELPER_SCHEMA
        and payload.get("status") == "PASS"
        and payload.get("engine") == "@oai/artifact-tool"
        and payload.get("production_written") is False
        and payload.get("source_workbook_sha256") == expected_sha256
        and payload.get("source_workbook_sha256_after") == expected_sha256
    ):
        raise LayoutExtractionError(
            "artifact-tool helper did not return a hash-stable PASS"
        )
    if payload.get("raw_block_ranges") != BLOCK_RANGES:
        raise LayoutExtractionError("helper raw block ranges are not the exact 18")
    if payload.get("sublayer_category_columns") != CATEGORY_SUBLAYER_COLUMNS:
        raise LayoutExtractionError(
            "helper sublayer category columns are not the exact 18"
        )
    error_scan = payload.get("formula_error_scan")
    if (
        not isinstance(error_scan, dict)
        or error_scan.get("match_count") != 0
        or error_scan.get("status") != "PASS"
    ):
        raise LayoutExtractionError("workbook formula-error scan did not pass")
    renders = payload.get("renders")
    if (
        not isinstance(renders, list)
        or len(renders) < 3
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("size"), int)
            or item["size"] <= 0
            or not HEX_RE.fullmatch(str(item.get("sha256") or ""))
            for item in renders
        )
    ):
        raise LayoutExtractionError("sample-range render evidence is incomplete")
    formula_audit = payload.get("sublayer_formula_audit")
    if not (
        isinstance(formula_audit, dict)
        and formula_audit.get("checked_cells") == roster.count() * 18
        and formula_audit.get("mismatch_count") == 0
    ):
        raise LayoutExtractionError(
            f"all {roster.count() * 18:,} category sublayer formulas were not verified"
        )
    extraction_basis = payload.get("extraction_basis")
    if extraction_basis == "full_artifact_inspection_plus_ooxml":
        press_formula_audit = payload.get("press_normalized_formula_audit")
        press_alignment = payload.get("press_literal_row_alignment")
        efficiency_mapping = payload.get("efficiency_mapping_evidence")
        semantic_equality = payload.get("semantic_equality")
        if not (
            isinstance(press_formula_audit, dict)
            and press_formula_audit.get("checked_cells") == roster.count() * 20
            and press_formula_audit.get("mismatch_count") == 0
            and press_formula_audit.get("strict_exact_let_formula_comparison")
            is True
            and isinstance(press_alignment, dict)
            and press_alignment.get("status")
            == "REQUIRES_EXPLICIT_POLICY_DECISION"
            and press_alignment.get("mismatch_count") == 5
            and press_alignment.get("member_join_applied") is False
            and isinstance(efficiency_mapping, dict)
            and efficiency_mapping.get("status")
            == "UNRESOLVED_MISSING_CU_TAX"
            and efficiency_mapping.get("identified_slot_count") == 17
            and efficiency_mapping.get("omitted_categories")
            == ["CU Tax Status"]
            and efficiency_mapping.get("inferred_merge") is False
            and isinstance(semantic_equality, dict)
            and semantic_equality.get("status") == "PASS"
            and semantic_equality.get("sheet_count") == 6
            and semantic_equality.get("ignored")
            == ["formula_cached_values"]
            and isinstance(semantic_equality.get("sheets"), dict)
            and set(semantic_equality["sheets"])
            == set(AUDITED_SEMANTIC_SHEETS)
            and all(
                isinstance(row, dict) and row.get("equal") is True
                for row in semantic_equality["sheets"].values()
            )
            and error_scan.get("formula_cache_errors_are_nonblocking")
            is True
            and error_scan.get("global_table_count") == 30
            and isinstance(
                error_scan.get("formula_cache_diagnostic_count"), int
            )
            and error_scan.get("formula_cache_diagnostic_count") >= 0
        ):
            raise LayoutExtractionError(
                "sidecar/OOXML fallback evidence closure is incomplete"
            )

    members = _validate_members(payload.get("members"))
    nonleg = payload.get("sublayer_nonlegislative_values")
    weights = payload.get("sublayer_legislative_weights")
    if not isinstance(nonleg, dict) or not isinstance(weights, dict):
        raise LayoutExtractionError("helper omitted extracted sublayer inputs")
    normalized_nonleg: dict[str, list[float]] = {}
    normalized_weights: dict[str, list[float]] = {}
    for category in CATEGORIES:
        values = nonleg.get(category)
        role_weights = weights.get(category)
        if not isinstance(values, list) or len(values) != roster.count():
            raise LayoutExtractionError(
                f"{category} nonlegislative vector is not {roster.count()} values"
            )
        if not isinstance(role_weights, list) or len(role_weights) != 5:
            raise LayoutExtractionError(
                f"{category} legislative weight vector is not 5 values"
            )
        normalized_nonleg[category] = [
            finite_number(item, label=f"{category} nonlegislative value")
            for item in values
        ]
        normalized_weights[category] = [
            finite_number(item, label=f"{category} legislative weight")
            for item in role_weights
        ]

    slots = _validate_efficiency_slots(payload.get("efficiency_slots"))
    return {
        "members": members,
        "sublayer_nonlegislative_values": normalized_nonleg,
        "sublayer_legislative_weights": normalized_weights,
        "efficiency_slots": slots,
        "formula_error_scan": error_scan,
        "sublayer_formula_audit": formula_audit,
        "press_normalized_formula_audit": payload.get(
            "press_normalized_formula_audit"
        ),
        "press_literal_row_alignment": payload.get(
            "press_literal_row_alignment"
        ),
        "efficiency_mapping_evidence": payload.get(
            "efficiency_mapping_evidence"
        ),
        "semantic_equality": payload.get("semantic_equality"),
        "extraction_basis": extraction_basis,
        "renders": renders,
        "workbook_inspections": payload.get("workbook_inspections") or [],
    }


def build_layout(
    payload: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
    raw_extraction_receipt: Mapping[str, Any],
    payload_expected_sha256: str | None = None,
    calibration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    expected_sha = str(baseline["workbook_receipt"]["sha256"])
    validated = validate_helper_payload(
        payload, expected_sha256=payload_expected_sha256 or expected_sha
    )
    slots = validated["efficiency_slots"]
    identified = [
        row for row in slots if row["status"] == "IDENTIFIED_FROM_WORKBOOK"
    ]
    unresolved = [row["column"] for row in slots if row["status"] == "UNRESOLVED"]
    identified_categories = {
        row["categories"][0] for row in identified if row["categories"]
    }
    omitted_categories = sorted(set(CATEGORIES) - identified_categories)
    missing_cu_tax_only = (
        len(identified) == 17
        and not unresolved
        and omitted_categories == ["CU Tax Status"]
    )
    press_alignment = validated.get("press_literal_row_alignment")
    press_policy_pending = (
        isinstance(press_alignment, dict)
        and press_alignment.get("status")
        == "REQUIRES_EXPLICIT_POLICY_DECISION"
    )
    blockers = [
        (
            "CU Tax Status has no AG:AW efficiency slot; its treatment "
            "requires explicit approval"
            if missing_cu_tax_only
            else "18-to-17 efficiency mapping requires explicit approval"
        )
    ]
    if press_policy_pending:
        blockers.append(
            "five-member literal press-row mismatch requires explicit "
            "legacy-preserve versus member-join policy approval"
        )
    return {
        "schema": SCHEMA,
        "status": "PASS",
        "engine": "@oai/artifact-tool",
        "xlsx_opened_by_python": False,
        "ooxml_package_read_only_audit_by_python": (
            validated.get("extraction_basis")
            == "full_artifact_inspection_plus_ooxml"
        ),
        "production_written": False,
        "audit_complete": True,
        "source_workbook_sha256": expected_sha,
        "source_workbook_basename": WORKBOOK_BASENAME,
        "source_binding": {
            "baseline_manifest": baseline["manifest_receipt"],
            "frozen_workbook_copy": baseline["workbook_receipt"],
            "production_source_declared_but_not_opened": baseline[
                "production_source_declared"
            ],
            "artifact_extraction": dict(raw_extraction_receipt),
            **(
                {
                    "zero_legislation_calibration": {
                        "capability": calibration["capability_receipt"],
                        "workbook": calibration["workbook_receipt"],
                        "payload": calibration["payload_receipt"],
                        "inspection": calibration["inspection_receipt"],
                        "producer": calibration["producer_receipt"],
                        "accepted_workbook_helper": calibration[
                            "accepted_helper_receipt"
                        ],
                        "proof": calibration["proof"],
                    }
                }
                if calibration is not None
                else {}
            ),
        },
        "formula_value_materialization": (
            "press_raw_reconstruction_with_exact_let_and_literal_row_formulas"
            if validated.get("extraction_basis")
            == "full_artifact_inspection_plus_ooxml"
            else (
                "verified_zero_legislation_calibration"
                if calibration is not None
                else "direct_frozen_workbook_import"
            )
        ),
        "raw_block_ranges": BLOCK_RANGES,
        "sublayer_category_columns": CATEGORY_SUBLAYER_COLUMNS,
        "members": validated["members"],
        "sublayer_nonlegislative_values": validated[
            "sublayer_nonlegislative_values"
        ],
        "sublayer_legislative_weights": validated[
            "sublayer_legislative_weights"
        ],
        "efficiency_slots": slots,
        "efficiency_mapping_18_to_17": {
            "status": (
                "UNRESOLVED_MISSING_CU_TAX"
                if missing_cu_tax_only
                else "UNRESOLVED"
            ),
            "approved_by_user": False,
            "physical_slot_count": 17,
            "category_count": 18,
            "workbook_identified_slot_count": len(identified),
            "unresolved_slots": unresolved,
            "omitted_categories": omitted_categories,
            "inferred_merge": False,
            "reason": (
                (
                    "AG:AW identify 17 unique categories and omit CU Tax "
                    "Status. No shared/mean slot is inferred."
                )
                if missing_cu_tax_only
                else (
                    "Workbook facts cannot approve which two of 18 "
                    "categories share one of the 17 physical efficiency slots."
                )
            ),
        },
        "numeric_plan_ready": False,
        "numeric_plan_blockers": blockers,
        "excluded_from_inference": [
            {
                "range": f"Sub Layer - Raw!P4:P{roster.last_row('Sub Layer - Raw')}",
                "reason": (
                    "CU-Bank Competitive Dynamics is non-legislative and is "
                    "not one of the 18 category sublayers."
                ),
            },
            {
                "range": f"Sub Layer - Raw!AG4:AW{roster.last_row('Sub Layer - Raw')}",
                "reason": (
                    "The 17 physical slots identify 17 categories; CU Tax "
                    "Status is omitted and no merge/aggregation is inferred."
                ),
            },
        ],
        "formula_error_scan": validated["formula_error_scan"],
        "sublayer_formula_audit": validated["sublayer_formula_audit"],
        "press_normalized_formula_audit": validated.get(
            "press_normalized_formula_audit"
        ),
        "press_literal_row_alignment": press_alignment,
        "efficiency_mapping_evidence": validated.get(
            "efficiency_mapping_evidence"
        ),
        "six_sheet_semantic_equality": validated.get(
            "semantic_equality"
        ),
        "renders": validated["renders"],
        "workbook_inspections": validated["workbook_inspections"],
    }


def _blocked_layout(
    message: str, *, baseline_manifest: Path, stage_dir: Path
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "BLOCKED",
        "engine": "@oai/artifact-tool",
        "xlsx_opened_by_python": False,
        "production_written": False,
        "audit_complete": False,
        "numeric_plan_ready": False,
        "writes": [],
        "error": message,
        "requested_baseline_manifest": str(baseline_manifest.resolve()),
        "stage_directory": str(stage_dir.resolve()),
    }


def assert_stage_safe(
    stage_dir: Path, *, baseline: Mapping[str, Any]
) -> Path:
    """Reject directories that could place audit output in protected inputs."""
    stage_dir = stage_dir.resolve()
    source_root_text = str(
        baseline["manifest"].get("source_root") or ""
    ).strip()
    if source_root_text and stage_dir == Path(source_root_text).resolve():
        raise LayoutExtractionError(
            "stage directory cannot be the production root"
        )
    declared_text = str(baseline.get("production_source_declared") or "").strip()
    if declared_text and stage_dir == Path(declared_text).resolve().parent:
        raise LayoutExtractionError(
            "stage directory cannot be the production workbook folder"
        )
    if any(
        stage_dir == protected or stage_dir.is_relative_to(protected)
        for protected in (
            baseline["workbook_path"].parent.resolve(),
            Path(baseline["manifest_receipt"]["path"]).parent.resolve(),
        )
    ):
        raise LayoutExtractionError(
            "stage directory cannot be an immutable baseline directory"
        )
    return stage_dir


def run(
    *,
    baseline_manifest: Path,
    stage_dir: Path,
    helper: Path,
    environ: Mapping[str, str] | None = None,
    calibration_capability: Path | None = None,
) -> dict[str, Any]:
    stage_dir = stage_dir.resolve()
    baseline_manifest = baseline_manifest.resolve()
    helper = helper.resolve()
    if not helper.is_file():
        raise LayoutExtractionError(f"artifact-tool helper is missing: {helper}")
    baseline = resolve_frozen_workbook(baseline_manifest)
    stage_dir = assert_stage_safe(stage_dir, baseline=baseline)
    stage_dir.mkdir(parents=True, exist_ok=True)
    raw_path = stage_dir / DEFAULT_RAW_NAME
    render_dir = stage_dir / "layout_renders"
    layout_path = stage_dir / DEFAULT_LAYOUT_NAME
    calibration = (
        resolve_zero_legislation_calibration(
            calibration_capability, baseline=baseline
        )
        if calibration_capability is not None
        else None
    )
    extraction_workbook = (
        calibration["workbook_path"]
        if calibration is not None
        else baseline["workbook_path"]
    )
    before = sha256_file(baseline["workbook_path"])
    extraction_before = sha256_file(extraction_workbook)
    if calibration is not None:
        sidecar = Path(
            str(calibration["workbook_path"]) + ".inspect.ndjson"
        )
        payload = helper_payload_from_full_artifact_inspection(
            sidecar,
            calibration=calibration,
            baseline_workbook_path=baseline["workbook_path"],
        )
        atomic_json(raw_path, payload)
        command = [
            "verified-sidecar-ooxml-fallback",
            str(sidecar.resolve()),
        ]
        (stage_dir / "artifact_tool.stdout.txt").write_text(
            (
                "Used the accepted full artifact inspection sidecar and "
                "read-only six-sheet OOXML semantic audit.\n"
            ),
            encoding="utf-8",
            newline="\n",
        )
        (stage_dir / "artifact_tool.stderr.txt").write_text(
            "", encoding="utf-8", newline="\n"
        )
    else:
        runtime = artifact_runtime(environ)
        command = [
            str(runtime["node"]),
            str(helper),
            str(extraction_workbook),
            str(raw_path),
            str(render_dir),
            json.dumps(helper_geometry(), sort_keys=True),
        ]
        # Only a report written by this invocation may satisfy the existence
        # check below; clear anything a previous run left behind.
        raw_path.unlink(missing_ok=True)
        if render_dir.is_dir():
            shutil.rmtree(render_dir)
        timeout_text = (environ if environ is not None else os.environ).get(
            HELPER_TIMEOUT_ENV, ""
        )
        try:
            timeout_seconds = float(timeout_text) if timeout_text else 0.0
        except ValueError:
            timeout_seconds = 0.0
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            timeout_seconds = float(DEFAULT_HELPER_TIMEOUT_SECONDS)
        try:
            result = subprocess.run(
                command,
                cwd=stage_dir,
                env=runtime["environment"],
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise LayoutExtractionError(
                f"artifact-tool helper timed out after {timeout_seconds:g}s"
            ) from exc
        (stage_dir / "artifact_tool.stdout.txt").write_text(
            result.stdout or "", encoding="utf-8", newline="\n"
        )
        (stage_dir / "artifact_tool.stderr.txt").write_text(
            result.stderr or "", encoding="utf-8", newline="\n"
        )
        if not raw_path.is_file():
            raise LayoutExtractionError(
                "artifact-tool helper did not materialize its extraction report"
            )
        payload = read_object(raw_path)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-2000:]
            raise LayoutExtractionError(
                "artifact-tool helper failed with exit "
                f"{result.returncode}: {detail}"
            )
    after = sha256_file(baseline["workbook_path"])
    extraction_after = sha256_file(extraction_workbook)
    if before != after or after != baseline["workbook_receipt"]["sha256"]:
        raise LayoutExtractionError("frozen workbook changed during extraction")
    if extraction_before != extraction_after:
        raise LayoutExtractionError(
            "calibration/extraction workbook changed during extraction"
        )
    layout = build_layout(
        payload,
        baseline=baseline,
        raw_extraction_receipt=file_receipt(raw_path),
        payload_expected_sha256=extraction_before,
        calibration=calibration,
    )
    atomic_json(layout_path, layout)
    return {
        "layout": layout,
        "layout_path": layout_path,
        "raw_path": raw_path,
        "command": command,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--baseline-manifest", type=Path, required=True)
    result.add_argument("--stage-dir", type=Path, required=True)
    result.add_argument(
        "--helper",
        type=Path,
        default=(
            Path(__file__).resolve().parent.parent
            / "extract_legislation_workbook_audited_layout_artifact.cjs"
        ),
    )
    result.add_argument(
        "--calibration-capability",
        type=Path,
        help=(
            "Verified zero-legislation artifact-tool capability used only to "
            "materialize blank frozen formula caches."
        ),
    )
    return result


def _write_blocked_if_safe(
    args: argparse.Namespace, layout_path: Path, message: str
) -> None:
    try:
        baseline = resolve_frozen_workbook(args.baseline_manifest)
        assert_stage_safe(args.stage_dir, baseline=baseline)
    except Exception:
        return
    try:
        args.stage_dir.resolve().mkdir(parents=True, exist_ok=True)
        atomic_json(
            layout_path,
            _blocked_layout(
                message,
                baseline_manifest=args.baseline_manifest,
                stage_dir=args.stage_dir,
            ),
        )
    except OSError:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    layout_path = args.stage_dir.resolve() / DEFAULT_LAYOUT_NAME
    try:
        result = run(
            baseline_manifest=args.baseline_manifest,
            stage_dir=args.stage_dir,
            helper=args.helper,
            calibration_capability=args.calibration_capability,
        )
    except LayoutExtractionError as exc:
        # Emit a machine-readable BLOCKED receipt only when the requested
        # destination is itself safe.  An unsafe stage path must remain
        # completely untouched.
        _write_blocked_if_safe(args, layout_path, str(exc))
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # fail closed: never leave a stale PASS layout
        message = f"{type(exc).__name__}: {exc}"
        _write_blocked_if_safe(args, layout_path, message)
        print(f"BLOCKED: {message}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": result["layout"]["status"],
                "layout": str(result["layout_path"]),
                "source_workbook_sha256": result["layout"][
                    "source_workbook_sha256"
                ],
                "members": len(result["layout"]["members"]),
                "numeric_plan_ready": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
