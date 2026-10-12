#!/usr/bin/env python3
"""Audit a Quorum workbook-derived recall floor against Congress.gov evidence.

Quorum is never accepted as the primary collection instrument.  This gate is
strictly one-way: every frozen floor identifier must be present in the
Congress.gov Advanced Search union or have an explicit preserved exclusion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FLOOR = (
    ROOT
    / "audits"
    / "legfix5"
    / "quorum_floor_20260726"
    / "quorum_floor_manifest.json"
)
DEFAULT_PRIMARY = (
    ROOT
    / "audits"
    / "legfix5"
    / "congress_advanced_pages_20260725"
)
DEFAULT_OUTPUT = (
    ROOT
    / "audits"
    / "legfix5"
    / "quorum_floor_20260726"
    / "reconciliation_receipt.json"
)
PRIMARY_INSTRUMENT = "Congress.gov Advanced Search - Legislation"


class FloorAuditError(RuntimeError):
    """Raised when the recall floor is not exactly reconciled."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise FloorAuditError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def read_ndjson(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, raw in enumerate(stream, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FloorAuditError(
                    f"invalid NDJSON at {path}:{line_number}: {exc}"
                ) from exc
            require(
                isinstance(value, dict),
                f"expected object at {path}:{line_number}",
            )
            yield value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def canonical_key(value: dict[str, Any]) -> str:
    direct = str(value.get("canonical_key") or value.get("identifier") or "")
    if direct:
        return direct.lower()
    congress = value.get("congress")
    bill_type = value.get("type")
    number = value.get("number")
    if congress is None or not bill_type or number is None:
        return ""
    return f"{int(congress)}:{str(bill_type).lower()}:{int(number)}"


def resolve_source_path(manifest_path: Path, source_value: str) -> Path:
    path = Path(source_value)
    if path.is_absolute():
        return path.resolve()
    return (manifest_path.parent / path).resolve()


def audit_floor(
    floor_path: Path,
    primary_dir: Path,
) -> dict[str, Any]:
    floor_path = floor_path.resolve()
    primary_dir = primary_dir.resolve()
    primary_manifest_path = primary_dir / "manifest.json"
    records_path = primary_dir / "congress_advanced_records.ndjson"
    exclusions_path = primary_dir / "exclusion_ledger.ndjson"
    for required_path in (
        floor_path,
        primary_manifest_path,
        records_path,
        exclusions_path,
    ):
        require(required_path.is_file(), f"missing required input: {required_path}")

    floor = read_json(floor_path)
    policy = floor.get("collection_policy") or {}
    counts = floor.get("counts") or {}
    require(
        floor.get("schema") == "acu-legislation-quorum-recall-floor-v1",
        "unexpected floor schema",
    )
    require(
        floor.get("status") == "FROZEN_FLOOR_NOT_PRIMARY",
        "floor manifest is not frozen/non-primary",
    )
    require(
        policy.get("primary_instrument") == PRIMARY_INSTRUMENT,
        "floor policy does not preserve Congress.gov as primary",
    )
    require(
        policy.get("quorum_used_as_primary") is False
        and policy.get("quorum_used_as_recall_floor") is True,
        "Quorum floor/primary policy is invalid",
    )
    require(
        policy.get("floor_is_proof_of_completeness") is False
        and policy.get("scoring_or_promotion_allowed") is False,
        "floor policy improperly allows completeness/scoring/promotion",
    )

    source_item = floor.get("source_workbook") or {}
    source_path = resolve_source_path(
        floor_path, str(source_item.get("path") or "")
    )
    require(source_path.is_file(), "frozen Quorum workbook is missing")
    require(
        source_path.stat().st_size == source_item.get("bytes")
        and sha256_file(source_path) == source_item.get("sha256"),
        "frozen Quorum workbook receipt drift",
    )

    floor_rows = floor.get("rows") or []
    unique_identifiers = floor.get("unique_identifiers") or []
    require(
        isinstance(floor_rows, list) and isinstance(unique_identifiers, list),
        "floor row/identifier collections are invalid",
    )
    row_keys = [canonical_key(row) for row in floor_rows]
    require(all(row_keys), "floor contains a row without an identifier")
    expected_identifiers = sorted(set(row_keys))
    require(
        unique_identifiers == expected_identifiers,
        "floor unique identifier set does not exactly close over source rows",
    )
    require(
        counts.get("source_rows") == len(floor_rows)
        and counts.get("unique_identifiers") == len(expected_identifiers)
        and counts.get("duplicate_identifiers")
        == len(row_keys) - len(expected_identifiers),
        "floor manifest counts drift",
    )
    identifier_payload = ("\n".join(expected_identifiers) + "\n").encode()
    require(
        floor.get("identifier_set_sha256") == sha256_bytes(identifier_payload),
        "floor identifier-set hash drift",
    )

    primary_manifest = read_json(primary_manifest_path)
    require(
        primary_manifest.get("schema")
        == "acu-congress-advanced-pages-manifest-v1"
        and primary_manifest.get("status") == "complete",
        "Congress.gov primary manifest is not complete/current",
    )
    require(
        primary_manifest.get("primary_instrument") == PRIMARY_INSTRUMENT
        and primary_manifest.get("quorum_used") is False,
        "primary collection instrument drift",
    )

    primary_keys: set[str] = set()
    for row in read_ndjson(records_path):
        key = canonical_key(row)
        require(key, "primary record lacks canonical key")
        require(key not in primary_keys, f"duplicate primary canonical key: {key}")
        primary_keys.add(key)
    require(
        len(primary_keys)
        == (primary_manifest.get("union") or {}).get("unique_record_count"),
        "primary record count does not match its manifest",
    )

    exclusions_by_key: dict[str, list[dict[str, Any]]] = {}
    for exclusion in read_ndjson(exclusions_path):
        key = canonical_key(exclusion)
        require(key, "exclusion row lacks canonical identifier")
        exclusions_by_key.setdefault(key, []).append(exclusion)

    dispositions = []
    unresolved = []
    for identifier in expected_identifiers:
        if identifier in primary_keys:
            disposition = "present_in_congress_primary_union"
            evidence_count = 1
        elif identifier in exclusions_by_key:
            disposition = "explicitly_excluded_with_preserved_evidence"
            evidence_count = len(exclusions_by_key[identifier])
        else:
            disposition = "unresolved_floor_miss"
            evidence_count = 0
            unresolved.append(identifier)
        dispositions.append(
            {
                "identifier": identifier,
                "disposition": disposition,
                "evidence_count": evidence_count,
            }
        )

    require(
        not unresolved,
        "Quorum recall floor is not closed: " + ", ".join(unresolved),
    )
    present_count = sum(
        row["disposition"] == "present_in_congress_primary_union"
        for row in dispositions
    )
    excluded_count = sum(
        row["disposition"]
        == "explicitly_excluded_with_preserved_evidence"
        for row in dispositions
    )
    return {
        "schema": "acu-legislation-quorum-recall-floor-audit-v1",
        "status": "PASS",
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "primary_instrument": PRIMARY_INSTRUMENT,
            "quorum_used_as_primary": False,
            "quorum_used_as_recall_floor": True,
            "floor_is_proof_of_completeness": False,
            "scoring_sync_promotion_authorized": False,
        },
        "inputs": {
            "floor_manifest": {
                "path": str(floor_path),
                "bytes": floor_path.stat().st_size,
                "sha256": sha256_file(floor_path),
            },
            "floor_workbook": {
                "path": str(source_path),
                "bytes": source_path.stat().st_size,
                "sha256": sha256_file(source_path),
            },
            "primary_manifest": {
                "path": str(primary_manifest_path),
                "bytes": primary_manifest_path.stat().st_size,
                "sha256": sha256_file(primary_manifest_path),
            },
            "primary_records": {
                "path": str(records_path),
                "bytes": records_path.stat().st_size,
                "sha256": sha256_file(records_path),
            },
            "exclusion_ledger": {
                "path": str(exclusions_path),
                "bytes": exclusions_path.stat().st_size,
                "sha256": sha256_file(exclusions_path),
            },
        },
        "counts": {
            "floor_source_rows": len(floor_rows),
            "floor_unique_identifiers": len(expected_identifiers),
            "primary_unique_records": len(primary_keys),
            "present_in_primary_union": present_count,
            "explicitly_excluded": excluded_count,
            "unresolved": 0,
        },
        "floor_identifier_set_sha256": floor["identifier_set_sha256"],
        "dispositions": dispositions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--floor", type=Path, default=DEFAULT_FLOOR)
    parser.add_argument("--primary-dir", type=Path, default=DEFAULT_PRIMARY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    receipt = audit_floor(arguments.floor, arguments.primary_dir)
    atomic_write_json(arguments.output.resolve(), receipt)
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "floor": receipt["counts"]["floor_unique_identifiers"],
                "present": receipt["counts"]["present_in_primary_union"],
                "excluded": receipt["counts"]["explicitly_excluded"],
                "unresolved": receipt["counts"]["unresolved"],
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
