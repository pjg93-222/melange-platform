# -*- coding: utf-8 -*-
"""Restricted source-native Politico normalization and conservative ER pilot."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from typing import Any, Mapping

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover
    from fec.lane_file_io import atomic_write_bytes

from .source_manifest import canonical_json_bytes, sha256_file, validate_manifest_files
from .validate import assert_public_safe


NORMALIZATION_SCHEMA_ID = "politico-directory-normalization-receipt-v1"
RESOLUTION_SCHEMA_ID = "politico-directory-resolution-pilot-receipt-v1"
NORMALIZER_VERSION = "politico-directory-normalizer-v1"
RESOLUTION_METHOD_VERSION = "politico-quorum-exact-name-title-office-pilot-v1"
RAW_FIELDS = {
    "issue_areas", "name", "partition", "partition_ordinal", "profile_uuid", "roles",
}
PARTIES = {"Democratic", "Republican", "Unknown", "Independent"}
PERSON_FIELDS = {
    "source_person_id", "source_profile_uuid", "display_name", "name_normalized",
    "party_partition", "partition_ordinal", "observed_at",
}
ROLE_FIELDS = {
    "assertion_id", "source_person_id", "source_profile_uuid", "predicate",
    "role_ordinal", "role_text", "observed_at",
}
ISSUE_FIELDS = {
    "assertion_id", "source_person_id", "source_profile_uuid", "predicate",
    "issue_ordinal", "issue_area", "observed_at",
}
CANDIDATE_FIELDS = {
    "candidate_id", "disposition", "source_profile_uuid", "politico_name",
    "politico_roles", "legacy_staff_id", "legacy_name", "legacy_title",
    "legacy_member_name", "title_match", "office_evidence_available",
    "office_match", "review_status",
}
_TITLE_STOP = {
    "a", "an", "and", "at", "for", "in", "of", "on", "the", "to",
    "office", "house", "senate", "committee", "caucus",
}
_OFFICE_MARKER = re.compile(r"\b(?:office of|committee on|house|senate|caucus)\b", re.I)


class PoliticoNormalizationError(RuntimeError):
    pass


def normalize_name(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    folded = re.sub(r"[^a-z0-9]+", " ", folded.casefold()).strip()
    tokens = [token for token in folded.split() if token not in {"jr", "sr", "ii", "iii", "iv"}]
    return " ".join(tokens)


def _tokens(value: str) -> set[str]:
    return {token for token in normalize_name(value).split() if token not in _TITLE_STOP}


def _title_match(title: str, roles: list[str]) -> bool:
    title_normalized = normalize_name(title)
    title_tokens = _tokens(title)
    if not title_tokens:
        return False
    for role in roles:
        role_normalized = normalize_name(role)
        role_tokens = _tokens(role)
        if title_normalized and title_normalized in role_normalized:
            return True
        union = title_tokens | role_tokens
        if union and len(title_tokens & role_tokens) / len(union) >= 0.75:
            return True
    return False


def _office_match(member_name: str, roles: list[str]) -> tuple[bool, bool]:
    comparable = any(_OFFICE_MARKER.search(role) for role in roles)
    member = normalize_name(member_name)
    matched = bool(member) and any(member in normalize_name(role) for role in roles)
    return comparable, matched


def _write_ndjson(path: Path, records: list[dict[str, Any]]) -> None:
    body = b"".join(canonical_json_bytes(record) for record in records)
    atomic_write_bytes(path, body)


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise PoliticoNormalizationError(f"{path.name}:{line_number}: blank line")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise PoliticoNormalizationError(f"{path.name}:{line_number}: non-object")
            values.append(value)
    return values


def _require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PoliticoNormalizationError(f"{label} must be a nonempty string")
    return value


def _validate_raw_row(row: Any, line_number: int) -> dict[str, Any]:
    label = f"directory_rows.ndjson:{line_number}"
    if not isinstance(row, dict) or set(row) != RAW_FIELDS:
        raise PoliticoNormalizationError(f"{label}: source row shape mismatch")
    _require_nonempty_string(row["profile_uuid"], f"{label}.profile_uuid")
    _require_nonempty_string(row["name"], f"{label}.name")
    if row["partition"] not in PARTIES:
        raise PoliticoNormalizationError(f"{label}.partition is not allowlisted")
    if (
        not isinstance(row["partition_ordinal"], int)
        or isinstance(row["partition_ordinal"], bool)
        or row["partition_ordinal"] < 1
    ):
        raise PoliticoNormalizationError(f"{label}.partition_ordinal must be positive")
    for field in ("roles", "issue_areas"):
        values = row[field]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise PoliticoNormalizationError(f"{label}.{field} must be nonempty strings")
    assert_public_safe(row)
    return row


def _normalization_receipt(root: Path) -> dict[str, Any]:
    receipt_path = root / "normalization_receipt.json"
    if not receipt_path.is_file():
        raise PoliticoNormalizationError("normalized snapshot lacks its receipt")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_id") != NORMALIZATION_SCHEMA_ID
        or receipt.get("status") != "PASS"
        or receipt.get("canonical_entity_merges") != 0
        or receipt.get("contact_findings") != 0
        or receipt.get("failures") != []
    ):
        raise PoliticoNormalizationError("normalized snapshot receipt is not a clean PASS")
    advertised = {entry.get("path"): entry for entry in receipt.get("files", [])}
    expected = {
        "persons.ndjson": receipt.get("normalized_persons"),
        "role_assertions.ndjson": receipt.get("role_assertions"),
        "issue_assertions.ndjson": receipt.get("issue_assertions"),
    }
    if set(advertised) != set(expected):
        raise PoliticoNormalizationError("normalized snapshot inventory is not closed")
    for name, expected_records in expected.items():
        target = root / name
        entry = advertised[name]
        if (
            not target.is_file()
            or target.stat().st_size != entry.get("bytes")
            or sha256_file(target) != entry.get("sha256")
            or entry.get("records") != expected_records
        ):
            raise PoliticoNormalizationError(f"normalized artifact failed verification: {name}")
    return receipt


def normalize_politico_snapshot(
    run_root: str | Path,
    manifest: Mapping[str, Any],
    output_root: str | Path,
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Normalize one sealed, enabled snapshot into immutable source assertions."""

    source_root = Path(run_root)
    output = Path(output_root)
    validate_manifest_files(manifest, source_root)
    if manifest.get("source_system_id") != "politico-pro-staffer-directory":
        raise PoliticoNormalizationError("unexpected source system")
    if manifest.get("authorization_status") != "AUTHORIZED":
        raise PoliticoNormalizationError("source is not authorized")
    if manifest.get("enabled") is not True:
        raise PoliticoNormalizationError("source is not enabled")
    if manifest.get("redistribution_class") not in {
        "public", "public-safe-derived-only"
    }:
        raise PoliticoNormalizationError("source is not enabled for derived analysis")
    if not isinstance(manifest.get("source_as_of"), str):
        raise PoliticoNormalizationError("source manifest lacks a dated source_as_of")
    if output.exists():
        raise PoliticoNormalizationError("immutable normalized output already exists")

    persons: list[dict[str, Any]] = []
    roles: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    seen_profiles: set[str] = set()
    with (source_root / "directory_rows.ndjson").open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise PoliticoNormalizationError(
                    f"directory_rows.ndjson:{line_number}: blank line"
                )
            row = _validate_raw_row(json.loads(line), line_number)
            profile_uuid = row["profile_uuid"]
            if profile_uuid in seen_profiles:
                raise PoliticoNormalizationError("duplicate source profile UUID")
            seen_profiles.add(profile_uuid)
            source_person_id = f"politico-pro-staffer-directory:person:{profile_uuid}"
            person = {
                "source_person_id": source_person_id,
                "source_profile_uuid": profile_uuid,
                "display_name": row["name"],
                "name_normalized": normalize_name(row["name"]),
                "party_partition": row["partition"],
                "partition_ordinal": row["partition_ordinal"],
                "observed_at": manifest["source_as_of"],
            }
            if set(person) != PERSON_FIELDS or not person["name_normalized"]:
                raise PoliticoNormalizationError(f"row {line_number}: invalid normalized person")
            assert_public_safe(person)
            persons.append(person)
            for ordinal, role_text in enumerate(row["roles"], start=1):
                assertion = {
                    "assertion_id": f"politico-pro-staffer-directory:role:{profile_uuid}:{ordinal}",
                    "source_person_id": source_person_id,
                    "source_profile_uuid": profile_uuid,
                    "predicate": "HAS_DIRECTORY_ROLE_TEXT",
                    "role_ordinal": ordinal,
                    "role_text": role_text,
                    "observed_at": manifest["source_as_of"],
                }
                if set(assertion) != ROLE_FIELDS:
                    raise PoliticoNormalizationError("role assertion shape mismatch")
                assert_public_safe(assertion)
                roles.append(assertion)
            for ordinal, issue_area in enumerate(row["issue_areas"], start=1):
                assertion = {
                    "assertion_id": f"politico-pro-staffer-directory:issue:{profile_uuid}:{ordinal}",
                    "source_person_id": source_person_id,
                    "source_profile_uuid": profile_uuid,
                    "predicate": "HAS_DIRECTORY_ISSUE_AREA",
                    "issue_ordinal": ordinal,
                    "issue_area": issue_area,
                    "observed_at": manifest["source_as_of"],
                }
                if set(assertion) != ISSUE_FIELDS:
                    raise PoliticoNormalizationError("issue assertion shape mismatch")
                assert_public_safe(assertion)
                issues.append(assertion)

    if len(seen_profiles) != manifest.get("accepted_count"):
        raise PoliticoNormalizationError(
            "normalized profile count does not reconcile to the source manifest"
        )

    staging_parent = output.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=staging_parent))
    try:
        _write_ndjson(staging / "persons.ndjson", persons)
        _write_ndjson(staging / "role_assertions.ndjson", roles)
        _write_ndjson(staging / "issue_assertions.ndjson", issues)
        files = [
            {
                "path": name,
                "bytes": (staging / name).stat().st_size,
                "sha256": sha256_file(staging / name),
                "records": count,
            }
            for name, count in (
                ("persons.ndjson", len(persons)),
                ("role_assertions.ndjson", len(roles)),
                ("issue_assertions.ndjson", len(issues)),
            )
        ]
        receipt = {
            "schema_id": NORMALIZATION_SCHEMA_ID,
            "generated_at": generated_at,
            "status": "PASS",
            "normalizer_version": NORMALIZER_VERSION,
            "source_manifest_sha256": manifest["manifest_sha256"],
            "source_rows_sha256": sha256_file(source_root / "directory_rows.ndjson"),
            "source_profiles": len(seen_profiles),
            "normalized_persons": len(persons),
            "role_assertions": len(roles),
            "issue_assertions": len(issues),
            "person_fields": sorted(PERSON_FIELDS),
            "role_fields": sorted(ROLE_FIELDS),
            "issue_fields": sorted(ISSUE_FIELDS),
            "contact_findings": 0,
            "files": files,
            "canonical_entity_merges": 0,
            "failures": [],
        }
        atomic_write_bytes(staging / "normalization_receipt.json", canonical_json_bytes(receipt))
        os.replace(staging, output)
        return receipt
    except BaseException:
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        raise


def resolution_pilot(
    normalized_root: str | Path,
    legacy_directory_path: str | Path,
    output_root: str | Path,
    *,
    generated_at: str,
    sample_size: int = 200,
) -> dict[str, Any]:
    """Create candidates only; never perform canonical identity merges."""

    normalized = Path(normalized_root)
    output = Path(output_root)
    if output.exists():
        raise PoliticoNormalizationError("immutable resolution-pilot output already exists")
    normalization_receipt = _normalization_receipt(normalized)
    persons = _read_ndjson(normalized / "persons.ndjson")
    role_rows = _read_ndjson(normalized / "role_assertions.ndjson")
    if len(persons) != normalization_receipt["normalized_persons"]:
        raise PoliticoNormalizationError("person count differs from normalization receipt")
    if len(role_rows) != normalization_receipt["role_assertions"]:
        raise PoliticoNormalizationError("role count differs from normalization receipt")
    roles_by_uuid: dict[str, list[str]] = defaultdict(list)
    for role in role_rows:
        if set(role) != ROLE_FIELDS:
            raise PoliticoNormalizationError("normalized role assertion shape mismatch")
        assert_public_safe(role)
        roles_by_uuid[role["source_profile_uuid"]].append(role["role_text"])
    for person in persons:
        if set(person) != PERSON_FIELDS:
            raise PoliticoNormalizationError("normalized person shape mismatch")
        assert_public_safe(person)
    legacy_document = json.loads(Path(legacy_directory_path).read_text(encoding="utf-8"))
    legacy_rows: list[dict[str, Any]] = []
    for member, rows in legacy_document.items():
        for row in rows:
            if find := (set(row) & {"e", "p", "email", "phone"}):
                raise PoliticoNormalizationError(
                    "legacy pilot input still contains contact fields: " + ",".join(sorted(find))
                )
            legacy_rows.append(
                {
                    "member_name": member,
                    "legacy_staff_id": str(row["id"]),
                    "display_name": row["n"],
                    "title": row["t"],
                    "name_normalized": normalize_name(row["n"]),
                }
            )
    politico_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    legacy_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for person in persons:
        politico_by_name[person["name_normalized"]].append(person)
    for row in legacy_rows:
        legacy_by_name[row["name_normalized"]].append(row)

    decisions: Counter[str] = Counter({
        "STRICT_CANDIDATE_NAME_TITLE_OFFICE": 0,
        "REVIEW_NAME_TITLE_NO_OFFICE_EVIDENCE": 0,
        "REVIEW_NAME_TITLE_OFFICE_CONFLICT": 0,
        "REVIEW_NAME_ONLY": 0,
        "REVIEW_NAME_COLLISION": 0,
    })
    candidates: list[dict[str, Any]] = []
    for normalized_name in sorted(set(politico_by_name) & set(legacy_by_name)):
        politico_rows = politico_by_name[normalized_name]
        legacy_matches = legacy_by_name[normalized_name]
        if len(politico_rows) != 1 or len(legacy_matches) != 1:
            decisions["REVIEW_NAME_COLLISION"] += len(politico_rows)
            continue
        person = politico_rows[0]
        legacy = legacy_matches[0]
        profile_roles = roles_by_uuid[person["source_profile_uuid"]]
        title_match = _title_match(legacy["title"], profile_roles)
        office_comparable, office_match = _office_match(legacy["member_name"], profile_roles)
        if title_match and office_match:
            disposition = "STRICT_CANDIDATE_NAME_TITLE_OFFICE"
        elif title_match and not office_comparable:
            disposition = "REVIEW_NAME_TITLE_NO_OFFICE_EVIDENCE"
        elif title_match:
            disposition = "REVIEW_NAME_TITLE_OFFICE_CONFLICT"
        else:
            disposition = "REVIEW_NAME_ONLY"
        decisions[disposition] += 1
        candidate = {
            "candidate_id": "candidate:" + hashlib.sha256(
                (person["source_profile_uuid"] + "|" + legacy["legacy_staff_id"]).encode("utf-8")
            ).hexdigest(),
            "disposition": disposition,
            "source_profile_uuid": person["source_profile_uuid"],
            "politico_name": person["display_name"],
            "politico_roles": profile_roles,
            "legacy_staff_id": legacy["legacy_staff_id"],
            "legacy_name": legacy["display_name"],
            "legacy_title": legacy["title"],
            "legacy_member_name": legacy["member_name"],
            "title_match": title_match,
            "office_evidence_available": office_comparable,
            "office_match": office_match,
            "review_status": "PENDING",
        }
        if set(candidate) != CANDIDATE_FIELDS:
            raise PoliticoNormalizationError("resolution candidate shape mismatch")
        candidates.append(candidate)

    strict = [
        row for row in candidates
        if row["disposition"] == "STRICT_CANDIDATE_NAME_TITLE_OFFICE"
    ]
    sample = sorted(
        strict,
        key=lambda row: hashlib.sha256(row["candidate_id"].encode("utf-8")).hexdigest(),
    )[:sample_size]
    exact_name_overlap_legacy_rows = sum(
        len(rows) for name, rows in legacy_by_name.items() if name in politico_by_name
    )
    legacy_outcomes = {
        "PENDING_UNIQUE_NAME_CANDIDATE": len(candidates),
        "REVIEW_NAME_COLLISION": exact_name_overlap_legacy_rows - len(candidates),
        "NO_EXACT_NAME_OVERLAP": len(legacy_rows) - exact_name_overlap_legacy_rows,
    }
    staging_parent = output.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=staging_parent))
    try:
        _write_ndjson(staging / "resolution_candidates.ndjson", candidates)
        _write_ndjson(staging / "strict_candidate_review_sample.ndjson", sample)
        receipt = {
            "schema_id": RESOLUTION_SCHEMA_ID,
            "generated_at": generated_at,
            "status": "REPORT",
            "method_version": RESOLUTION_METHOD_VERSION,
            "normalization_receipt_sha256": sha256_file(
                normalized / "normalization_receipt.json"
            ),
            "legacy_directory_sha256": sha256_file(legacy_directory_path),
            "politico_persons": len(persons),
            "legacy_staff_rows": len(legacy_rows),
            "exact_name_overlap_profiles": sum(
                len(rows) for name, rows in politico_by_name.items() if name in legacy_by_name
            ),
            "exact_name_overlap_legacy_rows": exact_name_overlap_legacy_rows,
            "candidate_count": len(candidates),
            "dispositions": dict(sorted(decisions.items())),
            "legacy_outcomes": legacy_outcomes,
            "strict_candidate_count": len(strict),
            "review_sample_count": len(sample),
            "review_sample_requested": sample_size,
            "adjudicated_sample_count": 0,
            "estimated_precision": None,
            "canonical_entity_merges": 0,
            "auto_merge_enabled": False,
            "adjudication_required": True,
            "candidate_file_sha256": sha256_file(staging / "resolution_candidates.ndjson"),
            "review_sample_sha256": sha256_file(staging / "strict_candidate_review_sample.ndjson"),
            "failures": [],
        }
        atomic_write_bytes(staging / "resolution_pilot_receipt.json", canonical_json_bytes(receipt))
        os.replace(staging, output)
        return receipt
    except BaseException:
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        raise
