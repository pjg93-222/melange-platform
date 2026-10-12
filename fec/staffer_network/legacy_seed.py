# -*- coding: utf-8 -*-
"""Authority-gated remediation of the restricted legacy Quorum directory.

This module is deliberately not a collector.  It cannot open the legacy file
until a sealed, enabled, AUTHORIZED source manifest is supplied by the caller.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover
    from fec.lane_file_io import atomic_write_bytes

from .source_manifest import canonical_json_bytes, sha256_file, validate_source_manifest
from .validate import assert_public_safe


class LegacySeedError(RuntimeError):
    pass


def _authorize(manifest: Mapping[str, Any]) -> None:
    validate_source_manifest(manifest)
    if not manifest.get("enabled") or manifest.get("authorization_status") != "AUTHORIZED":
        raise LegacySeedError("legacy seed remains authority-gated")
    if manifest.get("redistribution_class") != "restricted-local-only":
        raise LegacySeedError("legacy seed requires restricted-local-only scope")


def _require_restricted_output(
    output: Path, synced_roots: Iterable[str | Path]
) -> None:
    for root in synced_roots:
        synced = Path(root).resolve()
        if output == synced or output.is_relative_to(synced):
            raise LegacySeedError("legacy seed output must remain outside synced roots")


def strip_document(
    document: Mapping[str, Any],
    *,
    expected_rows: int | None = None,
    expected_offices: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Remove all compact contact fields and conserve office membership."""

    output: list[dict[str, Any]] = []
    rejected = 0
    represented: set[str] = set()
    for office_key, rows in document.items():
        if not isinstance(office_key, str) or not isinstance(rows, list):
            rejected += 1
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                rejected += 1
                continue
            source_id = str(row.get("id") or "").strip()
            display_name = str(row.get("n") or "").strip()
            title = str(row.get("t") or "").strip()
            if not source_id or not display_name or not title:
                rejected += 1
                continue
            stripped = {
                "source_system_id": "quorum-legacy-derived",
                "source_entity_id": source_id,
                "member_office_source_key": office_key,
                "display_name": display_name,
                "title": title,
                "featured": bool(row.get("star", False)),
            }
            assert_public_safe(stripped)
            output.append(stripped)
            represented.add(office_key)

    receipt = {
        "observed_rows": len(output) + rejected,
        "accepted_rows": len(output),
        "rejected_rows": rejected,
        "represented_offices": len(represented),
    }
    if expected_rows is not None and receipt["observed_rows"] != expected_rows:
        raise LegacySeedError("legacy row conservation failed")
    if expected_rows is not None and receipt["accepted_rows"] != expected_rows:
        raise LegacySeedError("non-vacuous stripped-seed acceptance failed")
    if expected_offices is not None and receipt["represented_offices"] != expected_offices:
        raise LegacySeedError("legacy office coverage failed")
    return output, receipt


def build_stripped_seed(
    path: str | Path,
    manifest: Mapping[str, Any],
    *,
    expected_source_sha256: str,
    expected_rows: int = 5773,
    expected_offices: int = 533,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Open and strip the restricted source only after the authority gate."""

    _authorize(manifest)
    source = Path(path)
    if sha256_file(source) != expected_source_sha256:
        raise LegacySeedError("legacy seed input hash mismatch")
    with source.open("r", encoding="utf-8") as stream:
        document = json.load(stream)
    if not isinstance(document, Mapping):
        raise LegacySeedError("legacy directory root must be an object")
    return strip_document(
        document, expected_rows=expected_rows, expected_offices=expected_offices
    )


def build_stripped_seed_generation(
    path: str | Path,
    manifest: Mapping[str, Any],
    output_root: str | Path,
    *,
    expected_source_sha256: str,
    generated_at: str,
    source_path_label: str,
    synced_roots: Iterable[str | Path],
    expected_rows: int = 5773,
    expected_offices: int = 533,
) -> dict[str, Any]:
    """Publish one immutable restricted source-native seed plus a safe receipt."""

    source = Path(path).resolve()
    output = Path(output_root).resolve()
    _require_restricted_output(output, synced_roots)
    if output.exists():
        raise LegacySeedError(f"legacy seed generation already exists: {output}")
    rows, counts = build_stripped_seed(
        source,
        manifest,
        expected_source_sha256=expected_source_sha256,
        expected_rows=expected_rows,
        expected_offices=expected_offices,
    )
    rows.sort(key=lambda row: (
        row["member_office_source_key"], row["source_entity_id"]
    ))
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        records_path = staging / "source_native_seed.ndjson"
        atomic_write_bytes(
            records_path,
            b"".join(canonical_json_bytes(row) for row in rows),
        )
        receipt = {
            "schema_id": "staffer-network-legacy-seed-receipt-v1",
            "generated_at": generated_at,
            "status": "PASS",
            "source_system_id": "quorum-legacy-derived",
            "source_path": source_path_label,
            "source_sha256": expected_source_sha256,
            "source_bytes": source.stat().st_size,
            "source_manifest_sha256": manifest["manifest_sha256"],
            **counts,
            "record_file": {
                "records": len(rows),
                "sha256": sha256_file(records_path),
                "bytes": records_path.stat().st_size,
            },
            "contact_fields_retained": 0,
            "source_native_ingest_enabled": True,
            "analysis_enabled": False,
            "canonical_use_enabled": False,
            "canonical_entity_merges": 0,
            "redistribution_class": "restricted-local-only",
            "raw_quorum_workbooks_authorized": False,
            "failures": [],
        }
        assert_public_safe(receipt)
        atomic_write_bytes(
            staging / "legacy_seed_receipt.json", canonical_json_bytes(receipt)
        )
        os.replace(staging, output)
        return receipt
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
