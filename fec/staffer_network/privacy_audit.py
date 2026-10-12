# -*- coding: utf-8 -*-
"""P-1 privacy/license remediation and repository audit for staff artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover
    from fec.lane_file_io import atomic_write_bytes

from .source_manifest import sha256_bytes, sha256_file, validate_source_manifest
from .validate import find_prohibited_fields, find_prohibited_values


PRIVACY_SCHEMA_ID = "staffer-network-privacy-license-receipt-v1"
REGISTRY_SCHEMA_ID = "staffer-network-legacy-artifact-registry-v1"
SOURCE_ALLOWED_FIELDS = {"id", "n", "t", "star", "dom", "dsrc"}
SURFACE_ALLOWED_FIELDS = SOURCE_ALLOWED_FIELDS | {"st", "why"}
_CONTACT_KEYS = {"e", "p", "email", "phone", "email_address", "phone_number"}
_RESTRICTED_RAW_BASENAMES = {
    "directory_rows.ndjson",
    "request_log.ndjson",
    "partition_inventory.json",
}


class PrivacyAuditError(RuntimeError):
    pass


def _strip_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    stripped: list[dict[str, Any]] = []
    removed = 0
    for row in rows:
        clean = {}
        for key, value in row.items():
            if str(key).casefold() in _CONTACT_KEYS:
                removed += 1
            else:
                clean[key] = value
        stripped.append(clean)
    return stripped, removed


def strip_legacy_directory_document(document: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    output: dict[str, Any] = {}
    removed = 0
    for member, rows in document.items():
        if not isinstance(rows, list):
            raise PrivacyAuditError("legacy staff directory values must be row lists")
        clean, count = _strip_rows(row for row in rows if isinstance(row, Mapping))
        if len(clean) != len(rows):
            raise PrivacyAuditError("legacy staff directory contains non-object rows")
        output[str(member)] = clean
        removed += count
    return output, removed


def strip_member_bundle_document(document: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    output = dict(document)
    members = output.get("members")
    if not isinstance(members, list):
        raise PrivacyAuditError("member desk bundle lacks members")
    clean_members: list[dict[str, Any]] = []
    removed = 0
    for member in members:
        if not isinstance(member, Mapping):
            raise PrivacyAuditError("member desk bundle contains a non-object member")
        clean_member = dict(member)
        staff = clean_member.get("staffdir") or []
        if not isinstance(staff, list):
            raise PrivacyAuditError("member staffdir must be a list")
        clean_member["staffdir"], count = _strip_rows(
            row for row in staff if isinstance(row, Mapping)
        )
        if len(clean_member["staffdir"]) != len(staff):
            raise PrivacyAuditError("member staffdir contains a non-object row")
        removed += count
        clean_members.append(clean_member)
    output["members"] = clean_members
    return output, removed


def strip_profile_wrapper_text(text: str) -> tuple[str, int]:
    prefix = "window.__melangeProfileMerge("
    suffix = ");"
    if not text.startswith(prefix) or not text.endswith(suffix):
        raise PrivacyAuditError("profile wrapper shape mismatch")
    payload = json.loads(text[len(prefix):-len(suffix)])
    members = payload.get("members_heavy")
    if not isinstance(members, list):
        raise PrivacyAuditError("profile wrapper lacks members_heavy")
    removed = 0
    clean_members: list[dict[str, Any]] = []
    for member in members:
        if not isinstance(member, Mapping):
            raise PrivacyAuditError("profile wrapper contains a non-object member")
        clean_member = dict(member)
        staff = clean_member.get("staffdir") or []
        if not isinstance(staff, list):
            raise PrivacyAuditError("profile staffdir must be a list")
        clean_member["staffdir"], count = _strip_rows(
            row for row in staff if isinstance(row, Mapping)
        )
        if len(clean_member["staffdir"]) != len(staff):
            raise PrivacyAuditError("profile staffdir contains a non-object row")
        removed += count
        clean_members.append(clean_member)
    payload["members_heavy"] = clean_members
    return prefix + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + suffix, removed


def remediate_synced_staff_contacts(repo_root: str | Path) -> dict[str, Any]:
    """Mechanically remove only direct-contact keys from three synced surfaces."""

    root = Path(repo_root)
    directory_path = root / "data" / "members" / "staff_directory.json"
    bundle_path = root / "member_desk_bundle.json"
    profiles_path = root / "cu-member-desk-profiles.js"

    directory = json.loads(directory_path.read_text(encoding="utf-8"))
    clean_directory, directory_removed = strip_legacy_directory_document(directory)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    clean_bundle, bundle_removed = strip_member_bundle_document(bundle)
    profile_text, profile_removed = strip_profile_wrapper_text(
        profiles_path.read_text(encoding="utf-8")
    )

    atomic_write_bytes(
        directory_path,
        json.dumps(clean_directory, ensure_ascii=True).encode("utf-8"),
    )
    atomic_write_bytes(
        bundle_path,
        json.dumps(clean_bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    atomic_write_bytes(profiles_path, profile_text.encode("utf-8"))
    return {
        "directory_contact_keys_removed": directory_removed,
        "bundle_contact_keys_removed": bundle_removed,
        "profile_contact_keys_removed": profile_removed,
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in (directory_path, bundle_path, profiles_path)
        ],
    }


def _staff_rows(path: Path, kind: str) -> list[Mapping[str, Any]]:
    if kind == "source-directory":
        document = json.loads(path.read_text(encoding="utf-8"))
        return [row for rows in document.values() for row in rows]
    if kind == "member-desk-bundle":
        document = json.loads(path.read_text(encoding="utf-8"))
        return [row for member in document["members"] for row in (member.get("staffdir") or [])]
    if kind == "profile-wrapper":
        text = path.read_text(encoding="utf-8")
        prefix = "window.__melangeProfileMerge("
        document = json.loads(text[len(prefix):-2])
        return [row for member in document["members_heavy"] for row in (member.get("staffdir") or [])]
    raise PrivacyAuditError(f"unknown staff artifact kind: {kind}")


def _artifact_audit(root: Path, relative: str, kind: str) -> dict[str, Any]:
    path = root / relative
    rows = _staff_rows(path, kind)
    allowed = SOURCE_ALLOWED_FIELDS if kind == "source-directory" else SURFACE_ALLOWED_FIELDS
    unexpected: set[str] = set()
    prohibited_paths = 0
    contact_rows = 0
    for row in rows:
        unexpected.update(set(row) - allowed)
        prohibited_paths += len(find_prohibited_fields(row))
        contact_rows += bool(find_prohibited_values(row))
    return {
        "path": relative,
        "kind": kind,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "staff_rows": len(rows),
        "allowed_fields": sorted(allowed),
        "unexpected_fields": sorted(unexpected),
        "prohibited_field_findings": prohibited_paths,
        "contact_pattern_rows": contact_rows,
    }


def _restricted_raw_leaks(root: Path) -> list[str]:
    leaks: list[str] = []
    skip_directories = {".git", ".pytest_cache", "__pycache__"}
    roots = [path for path in (root / "data", root / "source_data") if path.is_dir()]
    for scan_root in roots:
        for directory, dirnames, filenames in os.walk(scan_root):
            dirnames[:] = sorted(name for name in dirnames if name not in skip_directories)
            for name in sorted(filenames):
                if name in _RESTRICTED_RAW_BASENAMES:
                    leaks.append((Path(directory) / name).relative_to(root).as_posix())
    for name in _RESTRICTED_RAW_BASENAMES:
        if (root / name).is_file():
            leaks.append(name)
    return leaks


def privacy_license_receipt(
    repo_root: str | Path,
    registry: Mapping[str, Any],
    *,
    registry_path: str | Path,
    restricted_backup_root: str | Path,
    generated_at: str,
) -> dict[str, Any]:
    """Audit staff surfaces, source authority/license state, and legacy remediation."""

    root = Path(repo_root).resolve()
    if registry.get("schema_id") != REGISTRY_SCHEMA_ID:
        raise PrivacyAuditError("legacy registry schema mismatch")
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        raise PrivacyAuditError("legacy registry must contain exactly one known artifact")
    legacy = artifacts[0]
    backup = Path(restricted_backup_root) / legacy["restricted_backup_relative_path"]
    current = root / legacy["repository_path"]
    backup_verified = (
        backup.is_file()
        and backup.stat().st_size == legacy["original_bytes"]
        and sha256_file(backup) == legacy["original_sha256"]
    )
    current_verified = (
        current.is_file()
        and current.stat().st_size == legacy["remediated_bytes"]
        and sha256_file(current) == legacy["remediated_sha256"]
    )

    staff_artifacts = [
        _artifact_audit(root, "data/members/staff_directory.json", "source-directory"),
        _artifact_audit(root, "member_desk_bundle.json", "member-desk-bundle"),
        _artifact_audit(root, "cu-member-desk-profiles.js", "profile-wrapper"),
    ]
    prohibited_total = sum(item["prohibited_field_findings"] for item in staff_artifacts)
    contact_total = sum(item["contact_pattern_rows"] for item in staff_artifacts)
    unexpected_fields = sorted(
        {field for item in staff_artifacts for field in item["unexpected_fields"]}
    )

    manifest_paths = sorted((root / "data" / "staffer_network" / "policy").glob("*.json"))
    manifest_paths += sorted(
        (root / "data" / "staffer_network" / "reference").rglob("source_manifest.json")
    )
    manifest_policy_violations: list[str] = []
    manifest_states: list[dict[str, Any]] = []
    for path in manifest_paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema_id") != "staffer-network-source-manifest-v1":
            continue
        source_system_id = str(document.get("source_system_id") or "")
        # Audit-evidence manifests advertise this receipt or downstream receipts.
        # Excluding that explicitly named internal class prevents a recursive P-1
        # inventory while acquisition/reference source manifests remain exhaustive.
        if (
            source_system_id.startswith("staffer-network-")
            and source_system_id.endswith("-evidence")
        ):
            continue
        try:
            validate_source_manifest(document)
        except ValueError:
            manifest_policy_violations.append(path.relative_to(root).as_posix())
            continue
        redistribution = document["redistribution_class"]
        enabled = document["enabled"]
        authorization = document["authorization_status"]
        if redistribution == "restricted-local-only" and enabled:
            manifest_policy_violations.append(path.relative_to(root).as_posix())
        if authorization == "PROHIBITED" and enabled:
            manifest_policy_violations.append(path.relative_to(root).as_posix())
        if not str(document.get("license") or "").strip():
            manifest_policy_violations.append(path.relative_to(root).as_posix())
        manifest_states.append(
            {
                "path": path.relative_to(root).as_posix(),
                "authorization_status": authorization,
                "redistribution_class": redistribution,
                "enabled": enabled,
                "manifest_sha256": document["manifest_sha256"],
            }
        )

    leaks = _restricted_raw_leaks(root)
    failures: list[str] = []
    if not backup_verified:
        failures.append("restricted recovery copy does not match the registered original")
    if not current_verified:
        failures.append("remediated legacy artifact does not match the registry")
    if prohibited_total:
        failures.append("prohibited staff contact fields remain")
    if contact_total:
        failures.append("staff rows retain email or phone patterns")
    if unexpected_fields:
        failures.append("staff artifacts contain fields outside their allowlists")
    if manifest_policy_violations:
        failures.append("source manifest authority/license policy violation")
    if leaks:
        failures.append("restricted Politico raw files found in the synced repository")

    registry_path = Path(registry_path).resolve()
    return {
        "schema_id": PRIVACY_SCHEMA_ID,
        "generated_at": generated_at,
        "status": "PASS" if not failures else "FAIL",
        "repository_scope": ".",
        "staff_artifacts": staff_artifacts,
        "staff_rows_scanned": sum(item["staff_rows"] for item in staff_artifacts),
        "prohibited_field_findings": prohibited_total,
        "contact_pattern_rows": contact_total,
        "unexpected_staff_fields": unexpected_fields,
        "source_manifests": manifest_states,
        "source_manifest_count": len(manifest_states),
        "manifest_policy_violations": sorted(set(manifest_policy_violations)),
        "restricted_raw_leak_paths": leaks,
        "legacy_registry_path": registry_path.relative_to(root).as_posix(),
        "legacy_registry_sha256": sha256_file(registry_path),
        "legacy_original_backup_verified": backup_verified,
        "legacy_current_scrubbed": current_verified,
        "failures": failures,
    }


def registry_sha256(document: Mapping[str, Any]) -> str:
    return sha256_bytes((json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
