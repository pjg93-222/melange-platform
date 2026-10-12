# -*- coding: utf-8 -*-
"""Source authority, provenance, conservation, and non-vacuity contracts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any, Mapping
from urllib.parse import urlparse


SCHEMA_ID = "staffer-network-source-manifest-v1"
AUTHORIZATION_STATES = {"AUTHORIZED", "QUARANTINED", "PROHIBITED"}
REDISTRIBUTION_CLASSES = {
    "public",
    "public-safe-derived-only",
    "restricted-local-only",
    "prohibited",
}


class ManifestError(ValueError):
    """Raised when a source manifest cannot satisfy A-0/A-1/A-2."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOP_LEVEL_FIELDS = {
    "schema_id", "source_system_id", "enabled", "authority_scope", "terms_uri",
    "terms_checked_at", "authorization_status", "approved_access_mode",
    "redistribution_class", "source_as_of", "collected_at", "collector_version",
    "parser_version", "expected_count", "observed_count", "accepted_count",
    "rejected_count", "files", "manifest_sha256", "license", "query_or_inventory",
    "crawl_watermark", "retention_policy", "source_schema_version", "freshness_rule",
    "duplicate_count", "amendment_policy", "rejection_reasons", "acceptance_bar",
}


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON with a single trailing newline."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_sha256(document: Mapping[str, Any]) -> str:
    """Hash manifest content without a self-referential ``manifest_sha256``."""

    payload = dict(document)
    payload.pop("manifest_sha256", None)
    return sha256_bytes(canonical_json_bytes(payload))


def _nonempty(document: Mapping[str, Any], key: str, errors: list[str]) -> None:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key} must be a nonempty string")


def _datetime_value(document: Mapping[str, Any], key: str, errors: list[str], *, nullable: bool = False) -> None:
    value = document.get(key)
    if nullable and value is None:
        return
    if not isinstance(value, str):
        errors.append(f"{key} must be an ISO-8601 date-time")
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{key} must be an ISO-8601 date-time")
        return
    if parsed.tzinfo is None:
        errors.append(f"{key} must include a timezone")


def validate_source_manifest(document: Mapping[str, Any]) -> None:
    """Enforce immediate acquisition invariants.

    An enabled source must be explicitly authorized, fully traceable, exactly
    conserved, and non-vacuous.  A source-specific empirical coverage bar may
    still keep it quarantined after this structural validation passes.
    """

    errors: list[str] = []
    missing = sorted(_TOP_LEVEL_FIELDS - set(document))
    if missing:
        errors.append(f"missing manifest fields: {missing!r}")
    extras = sorted(set(document) - _TOP_LEVEL_FIELDS)
    if extras:
        errors.append(f"unexpected manifest fields: {extras!r}")
    if document.get("schema_id") != SCHEMA_ID:
        errors.append(f"schema_id must equal {SCHEMA_ID}")

    for key in (
        "source_system_id",
        "authority_scope",
        "license",
        "terms_uri",
        "terms_checked_at",
        "authorization_status",
        "approved_access_mode",
        "query_or_inventory",
        "retention_policy",
        "source_schema_version",
        "freshness_rule",
        "amendment_policy",
        "redistribution_class",
        "collected_at",
        "collector_version",
        "parser_version",
    ):
        _nonempty(document, key, errors)
    _datetime_value(document, "terms_checked_at", errors)
    _datetime_value(document, "collected_at", errors)
    _datetime_value(document, "source_as_of", errors, nullable=True)

    authorization = document.get("authorization_status")
    if authorization not in AUTHORIZATION_STATES:
        errors.append(f"unknown authorization_status: {authorization!r}")
    redistribution = document.get("redistribution_class")
    if redistribution not in REDISTRIBUTION_CLASSES:
        errors.append(f"unknown redistribution_class: {redistribution!r}")

    enabled = document.get("enabled")
    if not isinstance(enabled, bool):
        errors.append("enabled must be boolean")
    if enabled and authorization != "AUTHORIZED":
        errors.append("enabled source must be AUTHORIZED")
    if enabled and redistribution == "prohibited":
        errors.append("prohibited source cannot be enabled")

    terms_uri = document.get("terms_uri")
    if isinstance(terms_uri, str):
        parsed_terms = urlparse(terms_uri)
        if not parsed_terms.scheme or (
            parsed_terms.scheme in {"http", "https"} and not parsed_terms.netloc
        ):
            errors.append("terms_uri must be an absolute URI")

    counts: dict[str, int] = {}
    for key in ("observed_count", "accepted_count", "rejected_count", "duplicate_count"):
        value = document.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"{key} must be a nonnegative integer")
        else:
            counts[key] = value
    if all(key in counts for key in ("observed_count", "accepted_count", "rejected_count")):
        if counts["accepted_count"] + counts["rejected_count"] != counts["observed_count"]:
            errors.append("accepted_count + rejected_count must equal observed_count")
        if enabled and counts["accepted_count"] == 0:
            errors.append("enabled source cannot pass with zero accepted records")

    expected = document.get("expected_count")
    if expected is not None and (
        not isinstance(expected, int) or isinstance(expected, bool) or expected < 0
    ):
        errors.append("expected_count must be null or a nonnegative integer")
    if enabled and isinstance(expected, int) and counts.get("observed_count") != expected:
        errors.append("enabled exact inventory must reconcile expected_count to observed_count")

    crawl_watermark = document.get("crawl_watermark")
    if crawl_watermark is not None and (
        not isinstance(crawl_watermark, str) or not crawl_watermark.strip()
    ):
        errors.append("crawl_watermark must be null or a nonempty string")

    rejection_reasons = document.get("rejection_reasons")
    if not isinstance(rejection_reasons, Mapping):
        errors.append("rejection_reasons must be an object")
    else:
        reason_total = 0
        for reason, value in rejection_reasons.items():
            if not isinstance(reason, str) or not reason:
                errors.append("rejection reason keys must be nonempty strings")
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"rejection_reasons[{reason!r}] must be nonnegative")
            else:
                reason_total += value
        if "rejected_count" in counts and reason_total != counts["rejected_count"]:
            errors.append("rejection reason counts must sum to rejected_count")

    acceptance_bar = document.get("acceptance_bar")
    if not isinstance(acceptance_bar, Mapping) or set(acceptance_bar) != {
        "metric", "threshold", "comparison", "observed", "pass"
    }:
        errors.append(
            "acceptance_bar must contain metric, threshold, comparison, observed, and pass"
        )
    else:
        if not isinstance(acceptance_bar.get("metric"), str) or not acceptance_bar["metric"].strip():
            errors.append("acceptance_bar.metric must be nonempty")
        for key in ("threshold", "observed"):
            value = acceptance_bar.get(key)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                errors.append(f"acceptance_bar.{key} must be finite numeric or null")
        if not isinstance(acceptance_bar.get("pass"), bool):
            errors.append("acceptance_bar.pass must be boolean")
        comparison = acceptance_bar.get("comparison")
        if comparison not in {">=", "<=", "=", None}:
            errors.append("acceptance_bar.comparison must be >=, <=, =, or null")
        threshold = acceptance_bar.get("threshold")
        observed = acceptance_bar.get("observed")
        computable_values = all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in (threshold, observed)
        )
        if computable_values and comparison is not None:
            computed = {
                ">=": observed >= threshold,
                "<=": observed <= threshold,
                "=": observed == threshold,
            }[comparison]
            if acceptance_bar.get("pass") is not computed:
                errors.append("acceptance_bar.pass contradicts observed/comparison/threshold")
        elif acceptance_bar.get("pass") is True:
            errors.append("acceptance_bar.pass cannot be true without a computable comparison")
        if enabled and acceptance_bar.get("pass") is not True:
            errors.append("enabled source must pass its frozen acceptance bar")

    files = document.get("files")
    if not isinstance(files, list) or not files:
        errors.append("files must be a nonempty list")
    else:
        seen: set[str] = set()
        for index, entry in enumerate(files):
            if not isinstance(entry, Mapping):
                errors.append(f"files[{index}] must be an object")
                continue
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                errors.append(f"files[{index}].path must be nonempty")
            elif path in seen:
                errors.append(f"duplicate file path: {path}")
            else:
                seen.add(path)
                portable = PurePosixPath(path.replace("\\", "/"))
                if portable.is_absolute() or ".." in portable.parts or ":" in portable.parts[0]:
                    errors.append(f"files[{index}].path must be a safe relative path")
            digest = entry.get("sha256")
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                errors.append(f"files[{index}].sha256 must be a lowercase SHA-256")
            size = entry.get("bytes")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                errors.append(f"files[{index}].bytes must be nonnegative")
            entry_extras = sorted(set(entry) - {"path", "sha256", "bytes"})
            if entry_extras:
                errors.append(f"files[{index}] unexpected fields: {entry_extras!r}")

    supplied_digest = document.get("manifest_sha256")
    if not isinstance(supplied_digest, str) or _SHA256.fullmatch(supplied_digest) is None:
        errors.append("manifest_sha256 must be a lowercase SHA-256")
    elif supplied_digest != manifest_sha256(document):
        errors.append("manifest_sha256 does not match canonical content")

    if errors:
        raise ManifestError("; ".join(errors))


def seal_source_manifest(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return a validated manifest with its canonical content digest."""

    sealed = dict(document)
    sealed["manifest_sha256"] = manifest_sha256(sealed)
    validate_source_manifest(sealed)
    return sealed


def validate_manifest_files(document: Mapping[str, Any], base_directory: str | Path) -> None:
    """Validate every advertised byte count and SHA-256 under ``base_directory``."""

    validate_source_manifest(document)
    base = Path(base_directory).resolve()
    errors: list[str] = []
    for entry in document["files"]:
        target = (base / entry["path"]).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            errors.append(f"file escapes manifest base: {entry['path']}")
            continue
        if not target.is_file():
            errors.append(f"missing manifest file: {entry['path']}")
            continue
        if target.stat().st_size != entry["bytes"]:
            errors.append(f"byte count mismatch: {entry['path']}")
        if sha256_file(target) != entry["sha256"]:
            errors.append(f"SHA-256 mismatch: {entry['path']}")
    if errors:
        raise ManifestError("; ".join(errors))
