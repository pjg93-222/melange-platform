#!/usr/bin/env python3
"""Build a hash-bound, staged-only legislation relationship generation.

This module is deliberately not a production archive/workbook producer.  It
materializes the reviewed Congress.gov membership and relationship facts into
one deterministic ledger for downstream shadow recomputation.  It fails closed
unless every upstream coverage, review, and relationship gate has passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

try:
    from lane_file_io import atomic_write_bytes, read_ndjson_text
    from legislation_measure_key import (
        CANONICAL_KEY_RE,
        measure_weight,
        measure_weight_policy,
    )
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import atomic_write_bytes, read_ndjson_text
    from fec.legislation_measure_key import (
        CANONICAL_KEY_RE,
        measure_weight,
        measure_weight_policy,
    )


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "accepted-legislation-relationship-v1"
MANIFEST_SCHEMA = "accepted-legislation-generation-manifest-v1"
REVIEW_MANIFEST_SCHEMA = "acu-legislation-membership-review-v3"
HISTORICAL_ACCEPTANCE_SCHEMA = (
    "historical-completed-membership-acceptance-v1"
)
HISTORICAL_ACCEPTANCE_AUDIT_SCHEMA = (
    "billstatus-snapshot-divergence-audit-v1"
)
# DECAY_WEIGHT is a user-owned value map, not a derived constant: seating the
# 120th Congress needs an explicit entry here as well as the one-line bump in
# legislation_measure_key.LAST_SUPPORTED_CONGRESS.
DECAY_WEIGHT = {116: 0.16, 117: 0.33, 118: 0.66, 119: 1.00}
MEASURE_KEY_RE = CANONICAL_KEY_RE
BIOGUIDE_RE = re.compile(r"^[A-Z]\d{6}$")
ROLE_STRENGTH = {"sponsor": 3, "original_cosponsor": 2, "cosponsor": 1}
ROLE_WEIGHT_POLICY_SCENARIOS = {
    "archive_legacy_3_2_1": {
        "sponsor": 3.0,
        "original_cosponsor": 2.0,
        "cosponsor": 1.0,
    },
    "workbook_legacy_4_2_1": {
        "sponsor": 4.0,
        "original_cosponsor": 2.0,
        "cosponsor": 1.0,
    },
    "stance_legacy_1_1_1": {
        "sponsor": 1.0,
        "original_cosponsor": 1.0,
        "cosponsor": 1.0,
    },
}
REQUIRED_ACCEPTANCE_TRUE = (
    "registry_hash_matches_manifest",
    "query_page_reconciliation",
    "query_congress_and_type_dimension_reconciliation",
    "selected_queries_complete",
    "required_registry_queries_complete",
    "union_records_have_membership",
    "six_year_congress_scope_present",
    "required_legislation_types_present",
    "official_text_relevance_complete",
    "trusted_article_cross_reference_complete",
    "trusted_article_reverse_coverage_complete",
    "membership_review_complete",
    "relationship_enrichment_complete",
)
NON_AUTHORITATIVE_PATH_TOKENS = (
    "/billstatus_bulk_enrichment/review_sidecars/",
    "/billstatus_bulk_enrichment/structured_relevance_sidecars/",
    "/partial_review_prefill/",
    "/partial_review_queue/",
)
NON_AUTHORITATIVE_TRUE_FLAGS = (
    "review_prefill_only",
    "proposal_only",
    "partial_discovery_only",
)
NON_AUTHORITATIVE_FALSE_FLAGS = (
    "downstream_consumption_allowed",
    "decision_import_allowed",
    "scoring_input_allowed",
    "membership_disposition_authority",
    "ratification_authority",
    "scoring_authority",
    "usable_as_membership_review_ledger",
)


class GenerationGateError(ValueError):
    """Raised when an upstream or staging-only invariant is not satisfied."""


def non_authoritative_path(path: Path) -> bool:
    """Return whether *path* belongs to a proposal/prefill-only namespace."""

    normalized = "/" + str(path.resolve()).replace("\\", "/").casefold().lstrip("/")
    return any(token in normalized for token in NON_AUTHORITATIVE_PATH_TOKENS)


def non_authoritative_markers(value: dict[str, Any]) -> list[str]:
    """List explicit fields that deny review, ratification, or scoring authority."""

    markers = [
        name
        for name in NON_AUTHORITATIVE_TRUE_FLAGS
        if value.get(name) is True
    ]
    markers.extend(
        name
        for name in NON_AUTHORITATIVE_FALSE_FLAGS
        if value.get(name) is False
    )
    if str(value.get("ratification_state") or "").strip().upper() == (
        "NOT_RATIFIED"
    ):
        markers.append("ratification_state=NOT_RATIFIED")
    return sorted(markers)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a staged, canonical legislation relationship ledger. "
            "No production files are read by glob or written."
        )
    )
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--reconciliation-report", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path, required=True)
    parser.add_argument(
        "--review-ledger",
        type=Path,
        help="Default: review manifest directory / ledger_file basename.",
    )
    parser.add_argument(
        "--historical-membership-acceptance-manifest",
        type=Path,
        required=True,
        help=(
            "Hash-bound divergence audit containing the lossless historical "
            "membership acceptance gate."
        ),
    )
    parser.add_argument(
        "--historical-membership-acceptance-ledger",
        type=Path,
        help=(
            "Default: historical acceptance manifest output "
            "historical_membership_acceptance.path."
        ),
    )
    parser.add_argument("--relationships-manifest", type=Path, required=True)
    parser.add_argument(
        "--relationships-ledger",
        type=Path,
        help="Default: relationships manifest directory / edges_file.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Explicit staging output directory; repository root is forbidden.",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in read_ndjson_text(path).splitlines()
        if line.strip()
    ]


def json_line(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_rows_like_javascript(rows: list[dict[str, Any]]) -> str:
    """Reproduce ``stableHash(rows)`` used by the relationship collector."""

    body = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def write_text_atomic(path: Path, body: str) -> None:
    # The scratch name used to be fixed, so two generations writing the same
    # output clobbered each other's staging file.  ``atomic_write_bytes`` is
    # unique per process and per call, fsync'd, and retries the publish through
    # the OneDrive lock window.  Newlines stay LF: the body is encoded here
    # rather than written through text mode.
    atomic_write_bytes(path, body.encode("utf-8"))


def write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    lines = [json_line(row) for row in rows]
    write_text_atomic(path, "\n".join(lines) + ("\n" if lines else ""))


def write_json(path: Path, value: Any) -> None:
    write_text_atomic(
        path,
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )


def resolve_manifest_child(
    manifest_path: Path, manifest_value: Any, explicit: Path | None
) -> Path:
    if explicit is not None:
        return explicit.resolve()
    if not manifest_value:
        raise GenerationGateError(
            f"{manifest_path} does not name the required ledger file"
        )
    candidate = Path(str(manifest_value))
    if candidate.is_absolute():
        return candidate.resolve()
    local = (manifest_path.parent / candidate).resolve()
    if local.exists():
        return local
    # Some manifests store a repository-relative display path.
    repository_relative = (ROOT / candidate).resolve()
    if repository_relative.exists():
        return repository_relative
    return local


def resolve_receipt_path(
    manifest_path: Path,
    receipt: Any,
    explicit: Path | None,
    *,
    label: str,
) -> Path:
    if explicit is not None:
        return explicit.resolve()
    if not isinstance(receipt, dict) or not receipt.get("path"):
        raise GenerationGateError(
            f"{manifest_path} does not name the required {label}"
        )
    candidate = Path(str(receipt["path"]))
    if candidate.is_absolute():
        return candidate.resolve()
    return (manifest_path.parent / candidate).resolve()


def validate_file_receipt(
    path: Path,
    receipt: Any,
    *,
    expected_count: int | None = None,
    label: str,
) -> None:
    if not isinstance(receipt, dict):
        raise GenerationGateError(f"{label} receipt is missing")
    declared_size = receipt.get("bytes", receipt.get("size"))
    if not (
        Path(str(receipt.get("path") or "")).resolve() == path.resolve()
        and receipt.get("sha256") == sha256_file(path)
        and declared_size == path.stat().st_size
    ):
        raise GenerationGateError(f"{label} receipt is stale or invalid")
    if (
        expected_count is not None
        and receipt.get("record_count") != expected_count
    ):
        raise GenerationGateError(f"{label} record count receipt drifted")


def validate_out_directory(out_dir: Path, input_paths: Iterable[Path]) -> Path:
    out = out_dir.resolve()
    if out == ROOT.resolve():
        raise GenerationGateError("--out cannot be the repository root")
    audit_root = (ROOT / "audits" / "legfix5").resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not (
        out == audit_root
        or audit_root in out.parents
        or out == temp_root
        or temp_root in out.parents
    ):
        raise GenerationGateError(
            "--out must be under audits/legfix5 or the system temporary directory"
        )
    if out.name.lower().startswith("congress_bills_archive_"):
        raise GenerationGateError(
            "--out cannot use the live congress_bills_archive_* namespace"
        )
    if out.suffix.lower() == ".xlsx":
        raise GenerationGateError("--out must be a directory, not a workbook")
    for source in input_paths:
        if out == source.resolve():
            raise GenerationGateError("--out cannot overwrite an input path")
    return out


def completed_query_versions(
    run_manifest_path: Path,
    run_manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    registry_sha256 = str(run_manifest.get("registry_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", registry_sha256):
        raise GenerationGateError("run manifest registry hash is invalid")
    entries = run_manifest.get("queries")
    if not isinstance(entries, list):
        raise GenerationGateError("run manifest completed query list is missing")
    versions: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise GenerationGateError("run manifest query entry is malformed")
        query_id = entry.get("query_id")
        records_sha256 = str(entry.get("records_sha256") or "")
        if not (
            isinstance(query_id, str)
            and query_id
            and query_id not in versions
            and re.fullmatch(r"[0-9a-f]{64}", records_sha256)
        ):
            raise GenerationGateError(
                "run manifest query identity/hash is invalid"
            )
        result_total = entry.get("result_total")
        unique_count = entry.get("unique_record_count")
        if not (
            isinstance(result_total, int)
            and not isinstance(result_total, bool)
            and isinstance(unique_count, int)
            and not isinstance(unique_count, bool)
            and result_total == unique_count
            and result_total >= 0
        ):
            raise GenerationGateError(
                f"run manifest query total is not exact: {query_id}"
            )
        versions[query_id] = {
            "scope": "current_completed_manifest",
            "manifest_sha256": sha256_file(run_manifest_path),
            "registry_sha256": registry_sha256,
            "query_id": query_id,
            "query_records_sha256": records_sha256,
            "query_result_total": result_total,
            "query_unique_record_count": unique_count,
            "query_file": entry.get("query_file"),
            "query_completed": True,
        }
    return versions


def validate_raw_membership_partition(
    *,
    run_manifest_path: Path,
    run_manifest: dict[str, Any],
    raw_membership_rows: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]]]:
    union = run_manifest.get("union")
    if not isinstance(union, dict):
        raise GenerationGateError("run manifest union is missing")
    if union.get("membership_count") != len(raw_membership_rows):
        raise GenerationGateError("raw membership row count drifted")
    if union.get("memberships_sha256") != sha256_rows_like_javascript(
        raw_membership_rows
    ):
        raise GenerationGateError("raw membership content hash drifted")
    versions = completed_query_versions(run_manifest_path, run_manifest)
    raw_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in raw_membership_rows:
        natural_key = (
            str(row.get("canonical_key") or ""),
            str(row.get("query_id") or ""),
        )
        if (
            not natural_key[0]
            or not natural_key[1]
            or natural_key in raw_by_key
        ):
            raise GenerationGateError(
                "raw membership natural key is missing or duplicate"
            )
        if natural_key[1] not in versions:
            raise GenerationGateError(
                "raw membership query lacks a completed current query "
                f"version: {natural_key[0]}/{natural_key[1]}"
            )
        raw_by_key[natural_key] = row
    review_keys = {
        (
            str(row.get("canonical_key") or ""),
            str(row.get("query_id") or ""),
        )
        for row in review_rows
    }
    if len(review_keys) != len(review_rows):
        raise GenerationGateError(
            "review membership natural key is missing or duplicate"
        )
    review_not_raw = sorted(review_keys - set(raw_by_key))
    if review_not_raw:
        raise GenerationGateError(
            "review ledger contains memberships absent from final raw "
            f"membership union: {review_not_raw[:20]}"
        )
    raw_only_keys = set(raw_by_key) - review_keys
    invalid_raw_only = sorted(
        key
        for key in raw_only_keys
        if not (
            raw_by_key[key].get("record_kind") == "coverage_probe"
            and raw_by_key[key].get("scoring_category") is False
        )
    )
    if invalid_raw_only:
        raise GenerationGateError(
            "final raw memberships escaped review without an explicit "
            "non-scoring coverage-probe classification: "
            f"{invalid_raw_only[:20]}"
        )
    partition = {
        "raw_membership_count": len(raw_by_key),
        "reviewed_membership_count": len(review_keys),
        "non_dispositive_coverage_probe_count": len(raw_only_keys),
        "raw_memberships_missing_partition_count": 0,
        "review_memberships_absent_from_raw_count": 0,
        "lossless_partition_closed": (
            len(raw_by_key) == len(review_keys) + len(raw_only_keys)
        ),
    }
    if not partition["lossless_partition_closed"]:
        raise GenerationGateError("final raw/review membership partition failed")
    provenance = {
        key: {
            "historical_membership_only": False,
            "current_membership_present": True,
            "current_query_version": versions[key[1]],
        }
        for key in review_keys
    }
    return partition, provenance


def validate_historical_acceptance(
    *,
    acceptance_manifest_path: Path,
    acceptance_manifest: dict[str, Any],
    acceptance_ledger_path: Path,
    acceptance_rows: list[dict[str, Any]],
    run_manifest_path: Path,
    run_manifest: dict[str, Any],
    review_ledger_path: Path,
    review_rows: list[dict[str, Any]],
    membership_provenance: dict[tuple[str, str], dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    if acceptance_manifest.get("schema") != (
        HISTORICAL_ACCEPTANCE_AUDIT_SCHEMA
    ):
        raise GenerationGateError(
            "historical membership acceptance manifest schema drifted"
        )
    gate = acceptance_manifest.get(
        "historical_membership_acceptance_gate"
    )
    if not (
        isinstance(gate, dict)
        and gate.get("schema") == HISTORICAL_ACCEPTANCE_SCHEMA
        and gate.get("passed") is True
        and gate.get("lossless_partition_closed") is True
        and gate.get("scoring_requires_official_relevance_ratification")
        is True
        and gate.get(
            "historical_membership_can_score_without_ratification"
        )
        is False
    ):
        raise GenerationGateError(
            "historical membership acceptance gate did not PASS"
        )
    if gate.get("membership_count") != len(acceptance_rows):
        raise GenerationGateError(
            "historical membership acceptance row count drifted"
        )
    partition = acceptance_manifest.get("partition") or {}
    historical_summary = acceptance_manifest.get(
        "historical_only_evidence_summary"
    ) or {}
    summary_membership_count = sum(
        value
        for value in (
            historical_summary.get("memberships_by_query") or {}
        ).values()
        if isinstance(value, int) and not isinstance(value, bool)
    )
    summary_dispositions = (
        historical_summary.get("review_dispositions") or {}
    )
    if not (
        partition.get("historical_aggregate_proven_and_retained")
        == gate.get("candidate_count")
        and summary_membership_count == gate.get("membership_count")
        and summary_dispositions.get("unresolved", 0)
        == gate.get("unresolved_non_scoring_count")
        and summary_dispositions.get("exclude_false_positive", 0)
        == gate.get("official_exclusion_non_scoring_count")
        and summary_dispositions.get("include", 0)
        == (
            gate.get("officially_ratified_scoring_count", 0)
            + gate.get("unratified_include_non_scoring_count", 0)
        )
    ):
        raise GenerationGateError(
            "historical membership acceptance does not close against the "
            "source divergence partition"
        )
    output_receipt = (
        (acceptance_manifest.get("outputs") or {}).get(
            "historical_membership_acceptance"
        )
    )
    validate_file_receipt(
        acceptance_ledger_path,
        output_receipt,
        expected_count=len(acceptance_rows),
        label="historical membership acceptance ledger",
    )
    run_manifest_sha256 = sha256_file(run_manifest_path)
    if gate.get("current_manifest_sha256") != run_manifest_sha256:
        raise GenerationGateError(
            "historical membership acceptance is stale against final run"
        )
    if gate.get("current_registry_sha256") != run_manifest.get(
        "registry_sha256"
    ):
        raise GenerationGateError(
            "historical membership acceptance current registry drifted"
        )
    if gate.get("review_ledger_sha256") != sha256_file(review_ledger_path):
        raise GenerationGateError(
            "historical membership acceptance is stale against review"
        )
    review_by_key = {
        (
            str(row.get("canonical_key") or ""),
            str(row.get("query_id") or ""),
        ): row
        for row in review_rows
    }
    current_versions = completed_query_versions(
        run_manifest_path, run_manifest
    )
    accepted_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in acceptance_rows:
        natural_key = (
            str(row.get("canonical_key") or ""),
            str(row.get("query_id") or ""),
        )
        if (
            row.get("schema") != HISTORICAL_ACCEPTANCE_SCHEMA
            or row.get("historical_membership_only") is not True
            or row.get("candidate_retained") is not True
            or natural_key in accepted_by_key
        ):
            raise GenerationGateError(
                "historical membership acceptance row is malformed or "
                f"duplicate: {natural_key}"
            )
        review = review_by_key.get(natural_key)
        if review is None:
            raise GenerationGateError(
                "historical membership acceptance is absent from review: "
                f"{natural_key}"
            )
        if row.get("review_disposition") != review.get("disposition"):
            raise GenerationGateError(
                "historical membership acceptance/review disposition "
                f"drifted: {natural_key}"
            )
        provenance = row.get("membership_query_provenance")
        historical_version = (
            provenance.get("historical_query_version")
            if isinstance(provenance, dict)
            else None
        )
        current_version = (
            provenance.get("current_query_version")
            if isinstance(provenance, dict)
            else None
        )
        expected_current = current_versions.get(natural_key[1])
        if not (
            isinstance(provenance, dict)
            and provenance.get("historical_membership_only") is True
            and provenance.get("current_membership_present") is False
            and isinstance(historical_version, dict)
            and isinstance(current_version, dict)
            and historical_version.get("manifest_sha256")
            == gate.get("historical_manifest_sha256")
            and current_version.get("manifest_sha256")
            == run_manifest_sha256
            and expected_current is not None
            and current_version.get("registry_sha256")
            == expected_current["registry_sha256"]
            and current_version.get("query_records_sha256")
            == expected_current["query_records_sha256"]
        ):
            raise GenerationGateError(
                "historical/current membership query-version provenance "
                f"drifted: {natural_key}"
            )
        ratified = row.get("official_relevance_ratified") is True
        scoring_eligible = (
            row.get("current_scoring_evidence_eligible") is True
        )
        if scoring_eligible != (
            row.get("review_disposition") == "include" and ratified
        ):
            raise GenerationGateError(
                "historical membership scoring authority is inconsistent: "
                f"{natural_key}"
            )
        if ratified:
            receipt = row.get("review_receipt")
            if not (
                isinstance(receipt, dict)
                and review.get("official_relevance_ratified") is True
                and receipt.get("official_decision_source_url")
                == review.get("official_decision_source_url")
                and receipt.get("official_decision_evidence_sha256")
                == review.get("official_decision_evidence_sha256")
                and receipt.get("reviewer") == review.get("reviewer")
                and receipt.get("reviewed_at") == review.get("reviewed_at")
                and str(
                    receipt.get("official_decision_source_url") or ""
                ).startswith("https://www.congress.gov/")
                and re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(
                        receipt.get(
                            "official_decision_evidence_sha256"
                        )
                        or ""
                    ).lower(),
                )
                and str(receipt.get("reviewer") or "").strip()
                and str(receipt.get("reviewed_at") or "").strip()
            ):
                raise GenerationGateError(
                    "historical membership relevance ratification lacks "
                    f"official evidence: {natural_key}"
                )
        accepted_by_key[natural_key] = row
        membership_provenance[natural_key] = provenance
    if gate.get("candidate_count") != len(
        {key[0] for key in accepted_by_key}
    ):
        raise GenerationGateError(
            "historical acceptance candidate count drifted"
        )
    calculated_counts = {
        "unresolved_non_scoring_count": sum(
            row.get("acceptance_state")
            == "historical_visible_unresolved_non_scoring"
            for row in acceptance_rows
        ),
        "official_exclusion_non_scoring_count": sum(
            row.get("acceptance_state")
            == "historical_visible_official_exclusion_non_scoring"
            for row in acceptance_rows
        ),
        "officially_ratified_scoring_count": sum(
            row.get("current_scoring_evidence_eligible") is True
            for row in acceptance_rows
        ),
        "unratified_include_non_scoring_count": sum(
            row.get("acceptance_state")
            == "historical_visible_include_unratified_non_scoring"
            for row in acceptance_rows
        ),
    }
    if any(gate.get(key) != value for key, value in calculated_counts.items()):
        raise GenerationGateError(
            "historical membership acceptance state counts drifted"
        )
    return accepted_by_key


def validate_upstream(
    *,
    run_manifest_path: Path,
    run_manifest: dict[str, Any],
    reconciliation: dict[str, Any],
    review_manifest: dict[str, Any],
    review_rows: list[dict[str, Any]],
    relationships_manifest: dict[str, Any],
    relationship_rows: list[dict[str, Any]],
) -> None:
    if run_manifest.get("status") != "complete":
        raise GenerationGateError("discovery run manifest is not complete")
    if run_manifest.get("primary_instrument") != (
        "Congress.gov Advanced Search - Legislation"
    ):
        raise GenerationGateError("discovery did not use Congress.gov Advanced Search")
    if run_manifest.get("quorum_used") is not False:
        raise GenerationGateError("Quorum must not be part of discovery")

    acceptance = reconciliation.get("acceptance", {})
    if acceptance.get("passed") is not True:
        raise GenerationGateError("reconciliation acceptance did not PASS")
    failed = [
        name for name in REQUIRED_ACCEPTANCE_TRUE if acceptance.get(name) is not True
    ]
    if failed:
        raise GenerationGateError(
            "required reconciliation gates did not pass: " + ", ".join(failed)
        )
    if acceptance.get("membership_review_unresolved_count") != 0:
        raise GenerationGateError("reconciliation still has unresolved memberships")
    if acceptance.get("relationship_unresolved_measures") != 0:
        raise GenerationGateError("relationship verification remains unresolved")
    if acceptance.get("missing_sponsor_records"):
        raise GenerationGateError("reconciliation reports missing sponsor records")
    if acceptance.get("historical_archive_missing"):
        raise GenerationGateError("historical recall coverage is incomplete")
    if acceptance.get("missing_required_registry_query_ids"):
        raise GenerationGateError("required registry queries are incomplete")
    run_registry_hash = run_manifest.get("registry_sha256")
    if not run_registry_hash:
        raise GenerationGateError("run manifest has no registry hash")
    if acceptance.get("manifest_registry_sha256") != run_registry_hash:
        raise GenerationGateError("reconciliation/run registry hash mismatch")
    if acceptance.get("current_registry_sha256") != run_registry_hash:
        raise GenerationGateError("reconciliation is stale against the registry")

    if review_manifest.get("schema") != REVIEW_MANIFEST_SCHEMA:
        raise GenerationGateError(
            "membership review manifest is not the authoritative review schema"
        )
    review_markers = non_authoritative_markers(review_manifest)
    if review_markers:
        raise GenerationGateError(
            "membership review manifest explicitly denies downstream "
            "authority: " + ", ".join(review_markers)
        )
    if not (
        review_manifest.get("discovery_records_are_never_deleted") is True
        and review_manifest.get("automated_semantic_exclusion") is False
        and review_manifest.get(
            "official_text_signals_require_review_before_inclusion"
        )
        is True
        and review_manifest.get(
            "resolved_decisions_require_reproducible_official_evidence"
        )
        is True
    ):
        raise GenerationGateError(
            "membership review authority/safety contract is incomplete"
        )
    if review_manifest.get("promotion_gate_passed") is not True:
        raise GenerationGateError("membership review promotion gate did not pass")
    if review_manifest.get("unresolved_count") != 0:
        raise GenerationGateError("membership review manifest is unresolved")
    if review_manifest.get("discovery_manifest_sha256") != sha256_file(
        run_manifest_path
    ):
        raise GenerationGateError("membership review is stale against discovery")
    if review_manifest.get("registry_sha256") != run_registry_hash:
        raise GenerationGateError("membership review/run registry hash mismatch")
    if review_manifest.get("membership_count") != len(review_rows):
        raise GenerationGateError("membership review row count mismatch")
    unresolved = [
        row for row in review_rows if row.get("disposition") == "unresolved"
    ]
    if unresolved:
        raise GenerationGateError("membership review ledger contains unresolved rows")
    allowed_dispositions = {"include", "exclude_false_positive"}
    invalid_dispositions = sorted(
        {
            str(row.get("disposition"))
            for row in review_rows
            if row.get("disposition") not in allowed_dispositions
        }
    )
    if invalid_dispositions:
        raise GenerationGateError(
            "membership ledger has invalid disposition(s): "
            + ", ".join(invalid_dispositions)
        )
    for index, row in enumerate(review_rows, start=1):
        row_markers = non_authoritative_markers(row)
        if row_markers:
            raise GenerationGateError(
                "membership review ledger contains a proposal-only or "
                f"non-authoritative row at line {index}: "
                + ", ".join(row_markers)
            )
    membership_keys = [
        (row.get("canonical_key"), row.get("query_id")) for row in review_rows
    ]
    if len(membership_keys) != len(set(membership_keys)):
        raise GenerationGateError("membership review ledger has duplicate keys")

    if relationships_manifest.get("status") != "complete":
        raise GenerationGateError("relationship enrichment is not complete")
    if relationships_manifest.get("unresolved_measure_count") != 0:
        raise GenerationGateError("relationship enrichment manifest is unresolved")
    run_records_hash = run_manifest.get("union", {}).get("records_sha256")
    if not run_records_hash:
        raise GenerationGateError("run manifest has no union records hash")
    if relationships_manifest.get("discovery_records_sha256") != run_records_hash:
        raise GenerationGateError("relationship enrichment is stale against discovery")
    if relationships_manifest.get("edge_count") != len(relationship_rows):
        raise GenerationGateError("relationship edge count mismatch")
    expected_edge_hash = relationships_manifest.get("edges_sha256")
    if expected_edge_hash and expected_edge_hash != sha256_rows_like_javascript(
        relationship_rows
    ):
        raise GenerationGateError("relationship edge content hash mismatch")


def member_identity(edge: dict[str, Any]) -> tuple[str, str]:
    bioguide = str(edge.get("bioguide_id") or "").strip().upper()
    name = " ".join(str(edge.get("member") or "").split())
    if not BIOGUIDE_RE.fullmatch(bioguide):
        raise GenerationGateError(
            "relationship edge lacks a valid Bioguide ID "
            f"for {edge.get('canonical_key')}: {bioguide!r}"
        )
    if not name:
        raise GenerationGateError(
            f"relationship edge has no member name: {edge.get('canonical_key')}"
        )
    return f"bioguide:{bioguide}", name


def canonical_role(edge: dict[str, Any]) -> str:
    source_role = edge.get("role")
    status = edge.get("relationship_status")
    if status not in {"current", "withdrawn"}:
        raise GenerationGateError(
            f"invalid relationship status {status!r} for "
            f"{edge.get('canonical_key')}"
        )
    if source_role == "Sponsor":
        if status != "current":
            raise GenerationGateError("a Sponsor edge cannot be withdrawn")
        if edge.get("original_cosponsor") is not None:
            raise GenerationGateError(
                "a Sponsor edge must carry original_cosponsor=null"
            )
        return "sponsor"
    if source_role != "Cosponsor":
        raise GenerationGateError(
            f"invalid relationship role {source_role!r} for "
            f"{edge.get('canonical_key')}"
        )
    if not isinstance(edge.get("original_cosponsor"), bool):
        raise GenerationGateError(
            "a Cosponsor edge must carry an explicit boolean "
            f"original_cosponsor flag for {edge.get('canonical_key')}"
        )
    return (
        "original_cosponsor"
        if edge.get("original_cosponsor") is True
        else "cosponsor"
    )


def parse_measure_key(measure_key: Any) -> tuple[int, str, int]:
    match = MEASURE_KEY_RE.fullmatch(str(measure_key or ""))
    if not match:
        raise GenerationGateError(f"invalid measure key: {measure_key!r}")
    return int(match.group(1)), match.group(2), int(match.group(3))


def relationship_rank(edge: dict[str, Any]) -> tuple[int, str]:
    role = canonical_role(edge)
    # The JSON tie-break is deterministic and carries no scoring meaning.
    return ROLE_STRENGTH[role], json_line(edge)


def make_row(
    membership: dict[str, Any],
    edge: dict[str, Any],
    role: str,
    *,
    input_hashes: dict[str, str],
    membership_query_provenance: dict[str, Any],
    historical_acceptance: dict[str, Any] | None,
) -> dict[str, Any]:
    measure_key = str(membership["canonical_key"])
    congress, measure_type, measure_number = parse_measure_key(measure_key)
    if edge.get("canonical_key") != measure_key:
        raise GenerationGateError("membership/relationship measure key mismatch")
    edge_congress = edge.get("congress")
    edge_type = str(edge.get("type") or "").lower()
    try:
        edge_number = int(edge.get("number"))
    except (TypeError, ValueError):
        edge_number = None
    if (edge_congress, edge_type, edge_number) != (
        congress,
        measure_type,
        measure_number,
    ):
        raise GenerationGateError(
            f"relationship identity fields conflict with {measure_key}"
        )
    _, member_name = member_identity(edge)
    member_bioguide = str(edge.get("bioguide_id") or "").strip().upper() or None
    membership_status = (
        "included"
        if membership["disposition"] == "include"
        else "excluded"
    )
    status = edge["relationship_status"]
    scoring_category = membership.get("scoring_category") is True
    historical_scoring_authorized = (
        historical_acceptance is None
        or historical_acceptance.get(
            "current_scoring_evidence_eligible"
        )
        is True
    )
    scoring_eligible = (
        membership_status == "included"
        and status == "current"
        and scoring_category
        and historical_scoring_authorized
    )
    decay = DECAY_WEIGHT[congress]
    measure_factor = measure_weight(measure_key)
    effective_weight = round(decay * measure_factor, 12)
    raw_scenarios = {
        policy: weights[role] if scoring_eligible else 0.0
        for policy, weights in ROLE_WEIGHT_POLICY_SCENARIOS.items()
    }
    decayed_scenarios = {
        policy: round(weight * effective_weight, 12)
        for policy, weight in raw_scenarios.items()
    }
    if membership_status == "excluded":
        zero_reason = "membership_excluded"
    elif status == "withdrawn":
        zero_reason = "relationship_withdrawn"
    elif not scoring_category:
        zero_reason = "category_non_scoring"
    elif not historical_scoring_authorized:
        zero_reason = "historical_membership_not_officially_ratified"
    else:
        zero_reason = None
    return {
        "schema": SCHEMA,
        "measure_key": measure_key,
        "congress": congress,
        "measure_type": measure_type,
        "measure_number": measure_number,
        "display_number": membership.get("display_number")
        or edge.get("display_number"),
        "measure_title": membership.get("title") or edge.get("title"),
        "official_title": membership.get("official_title"),
        "official_summary": membership.get("official_summary"),
        "policy_area": membership.get("policy_area"),
        "initial_action": membership.get("measure_initial_action"),
        "initial_action_date": membership.get(
            "measure_initial_action_date"
        ),
        "initial_action_date_iso": membership.get(
            "measure_initial_action_date_iso"
        ),
        "latest_action": membership.get("measure_latest_action"),
        "latest_action_date": membership.get(
            "measure_latest_action_date"
        ),
        "tracker_status": membership.get("measure_tracker_status"),
        "reported_cosponsor_count": membership.get(
            "measure_cosponsor_count"
        ),
        "committees": membership.get("measure_committees"),
        "amends": membership.get("measure_amends"),
        "category": membership["query_id"],
        "category_label": membership.get("category_label"),
        "scoring_category": scoring_category,
        "member_bioguide_id": member_bioguide,
        "member_name": member_name,
        "role": role,
        "relationship_status": status,
        "membership_status": membership_status,
        "historical_membership_only": (
            historical_acceptance is not None
        ),
        "historical_relevance_ratified": (
            historical_acceptance.get("official_relevance_ratified")
            if historical_acceptance is not None
            else None
        ),
        "original_cosponsor": edge.get("original_cosponsor") is True,
        "decay_weight": decay,
        "measure_weight": measure_factor,
        "effective_weight": effective_weight,
        "scoring_eligible": scoring_eligible,
        "zero_weight_reason": zero_reason,
        "role_weight_scenarios": raw_scenarios,
        "decayed_role_weight_scenarios": decayed_scenarios,
        "provenance": {
            "input_hashes": input_hashes,
            "membership": {
                "decision_source": membership.get("decision_source"),
                "decision_type": membership.get("decision_type"),
                "reason_code": membership.get("reason_code"),
                "reviewer": membership.get("reviewer"),
                "reviewed_at": membership.get("reviewed_at"),
                "congress_gov_url": membership.get("congress_gov_url"),
                "official_source_url": membership.get(
                    "official_decision_source_url"
                ),
                "official_evidence_sha256": membership.get(
                    "official_decision_evidence_sha256"
                ),
                "identity_mismatch_expected_canonical_key": (
                    membership.get(
                        "identity_mismatch_expected_canonical_key"
                    )
                ),
                "identity_mismatch_observed_canonical_key": (
                    membership.get(
                        "identity_mismatch_observed_canonical_key"
                    )
                ),
                "membership_query_provenance": (
                    membership_query_provenance
                ),
                "historical_acceptance_state": (
                    historical_acceptance.get("acceptance_state")
                    if historical_acceptance is not None
                    else None
                ),
            },
            "relationship": {
                "source_url": edge.get("source_url"),
                "sponsorship_date": edge.get("sponsorship_date"),
                "withdrawn_date": edge.get("withdrawn_date"),
                "withdrawal_explanation": edge.get(
                    "withdrawal_explanation"
                ),
                "collector_role": edge.get("role"),
                "collector_original_cosponsor": edge.get(
                    "original_cosponsor"
                ),
            },
        },
    }


def build_rows(
    review_rows: list[dict[str, Any]],
    relationship_rows: list[dict[str, Any]],
    *,
    input_hashes: dict[str, str],
    membership_query_provenance: dict[
        tuple[str, str], dict[str, Any]
    ],
    historical_acceptance: dict[
        tuple[str, str], dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    memberships_by_measure: dict[str, list[dict[str, Any]]] = {}
    for membership in review_rows:
        parse_measure_key(membership.get("canonical_key"))
        if not membership.get("query_id"):
            raise GenerationGateError("membership has no category/query_id")
        natural_key = (
            str(membership["canonical_key"]),
            str(membership["query_id"]),
        )
        if natural_key not in membership_query_provenance:
            raise GenerationGateError(
                "membership query-version provenance is missing: "
                f"{natural_key}"
            )
        memberships_by_measure.setdefault(
            membership["canonical_key"], []
        ).append(membership)

    edges_by_measure: dict[str, list[dict[str, Any]]] = {}
    for edge in relationship_rows:
        parse_measure_key(edge.get("canonical_key"))
        canonical_role(edge)
        member_identity(edge)
        edges_by_measure.setdefault(edge["canonical_key"], []).append(edge)

    missing_relationships = sorted(set(memberships_by_measure) - set(edges_by_measure))
    if missing_relationships:
        raise GenerationGateError(
            "reviewed measures have no relationship edges: "
            + ", ".join(missing_relationships[:20])
        )
    unreviewed_relationships = sorted(set(edges_by_measure) - set(memberships_by_measure))
    if unreviewed_relationships:
        raise GenerationGateError(
            "relationship measures have no reviewed membership: "
            + ", ".join(unreviewed_relationships[:20])
        )
    invalid_sponsor_counts = {
        measure_key: sum(
            canonical_role(edge) == "sponsor"
            for edge in edges
        )
        for measure_key, edges in edges_by_measure.items()
    }
    invalid_sponsor_counts = {
        measure_key: count
        for measure_key, count in invalid_sponsor_counts.items()
        if count != 1
    }
    if invalid_sponsor_counts:
        first_measure = sorted(invalid_sponsor_counts)[0]
        raise GenerationGateError(
            "relationship enrichment must contain exactly one current "
            f"Sponsor edge per measure; {first_measure} has "
            f"{invalid_sponsor_counts[first_measure]}"
        )

    output: list[dict[str, Any]] = []
    for measure_key in sorted(memberships_by_measure):
        edges = edges_by_measure[measure_key]
        current: dict[str, dict[str, Any]] = {}
        withdrawn: dict[str, dict[str, Any]] = {}
        for edge in edges:
            identity, _ = member_identity(edge)
            bucket = (
                current
                if edge["relationship_status"] == "current"
                else withdrawn
            )
            prior = bucket.get(identity)
            if prior is None or relationship_rank(edge) > relationship_rank(prior):
                bucket[identity] = edge

        for membership in sorted(
            memberships_by_measure[measure_key],
            key=lambda row: str(row["query_id"]),
        ):
            for status_bucket in (current, withdrawn):
                for identity in sorted(status_bucket):
                    edge = status_bucket[identity]
                    output.append(
                        make_row(
                            membership,
                            edge,
                            canonical_role(edge),
                            input_hashes=input_hashes,
                            membership_query_provenance=(
                                membership_query_provenance[
                                    (
                                        str(membership["canonical_key"]),
                                        str(membership["query_id"]),
                                    )
                                ]
                            ),
                            historical_acceptance=(
                                historical_acceptance.get(
                                    (
                                        str(membership["canonical_key"]),
                                        str(membership["query_id"]),
                                    )
                                )
                            ),
                        )
                    )

    output.sort(
        key=lambda row: (
            row["measure_key"],
            row["category"],
            row["member_bioguide_id"] or "",
            row["member_name"].casefold(),
            0 if row["relationship_status"] == "current" else 1,
            -ROLE_STRENGTH[row["role"]],
        )
    )
    natural_keys = [
        (
            row["measure_key"],
            row["category"],
            row["member_bioguide_id"] or row["member_name"].casefold(),
            row["relationship_status"],
        )
        for row in output
    ]
    if len(natural_keys) != len(set(natural_keys)):
        raise GenerationGateError("canonical output still has duplicate relationships")
    return output


def build_generation(
    *,
    run_manifest_path: Path,
    reconciliation_report_path: Path,
    review_manifest_path: Path,
    historical_membership_acceptance_manifest_path: Path,
    relationships_manifest_path: Path,
    out_dir: Path,
    review_ledger_path: Path | None = None,
    historical_membership_acceptance_ledger_path: Path | None = None,
    relationships_ledger_path: Path | None = None,
) -> dict[str, Any]:
    run_manifest_path = run_manifest_path.resolve()
    reconciliation_report_path = reconciliation_report_path.resolve()
    review_manifest_path = review_manifest_path.resolve()
    historical_membership_acceptance_manifest_path = (
        historical_membership_acceptance_manifest_path.resolve()
    )
    relationships_manifest_path = relationships_manifest_path.resolve()
    run_manifest = read_json(run_manifest_path)
    reconciliation = read_json(reconciliation_report_path)
    review_manifest = read_json(review_manifest_path)
    historical_membership_acceptance_manifest = read_json(
        historical_membership_acceptance_manifest_path
    )
    relationships_manifest = read_json(relationships_manifest_path)
    raw_memberships_path = resolve_manifest_child(
        run_manifest_path,
        (run_manifest.get("union") or {}).get("memberships_file"),
        None,
    )
    review_ledger_path = resolve_manifest_child(
        review_manifest_path,
        review_manifest.get("ledger_file"),
        review_ledger_path,
    )
    historical_membership_acceptance_ledger_path = resolve_receipt_path(
        historical_membership_acceptance_manifest_path,
        (
            historical_membership_acceptance_manifest.get("outputs") or {}
        ).get("historical_membership_acceptance"),
        historical_membership_acceptance_ledger_path,
        label="historical membership acceptance ledger",
    )
    relationships_ledger_path = resolve_manifest_child(
        relationships_manifest_path,
        relationships_manifest.get("edges_file"),
        relationships_ledger_path,
    )
    input_paths = [
        run_manifest_path,
        raw_memberships_path,
        reconciliation_report_path,
        review_manifest_path,
        review_ledger_path,
        historical_membership_acceptance_manifest_path,
        historical_membership_acceptance_ledger_path,
        relationships_manifest_path,
        relationships_ledger_path,
    ]
    non_authoritative_inputs = [
        path for path in input_paths if non_authoritative_path(path)
    ]
    if non_authoritative_inputs:
        raise GenerationGateError(
            "proposal/relevance/prefill/queue sidecars cannot be accepted "
            "generation inputs before authoritative ratification: "
            + ", ".join(str(path) for path in non_authoritative_inputs)
        )
    missing = [str(path) for path in input_paths if not path.is_file()]
    if missing:
        raise GenerationGateError("missing input file(s): " + ", ".join(missing))
    output_dir = validate_out_directory(out_dir, input_paths)
    raw_membership_rows = read_ndjson(raw_memberships_path)
    review_rows = read_ndjson(review_ledger_path)
    historical_membership_acceptance_rows = read_ndjson(
        historical_membership_acceptance_ledger_path
    )
    relationship_rows = read_ndjson(relationships_ledger_path)
    validate_upstream(
        run_manifest_path=run_manifest_path,
        run_manifest=run_manifest,
        reconciliation=reconciliation,
        review_manifest=review_manifest,
        review_rows=review_rows,
        relationships_manifest=relationships_manifest,
        relationship_rows=relationship_rows,
    )
    membership_partition, membership_query_provenance = (
        validate_raw_membership_partition(
            run_manifest_path=run_manifest_path,
            run_manifest=run_manifest,
            raw_membership_rows=raw_membership_rows,
            review_rows=review_rows,
        )
    )
    historical_acceptance = validate_historical_acceptance(
        acceptance_manifest_path=(
            historical_membership_acceptance_manifest_path
        ),
        acceptance_manifest=historical_membership_acceptance_manifest,
        acceptance_ledger_path=(
            historical_membership_acceptance_ledger_path
        ),
        acceptance_rows=historical_membership_acceptance_rows,
        run_manifest_path=run_manifest_path,
        run_manifest=run_manifest,
        review_ledger_path=review_ledger_path,
        review_rows=review_rows,
        membership_provenance=membership_query_provenance,
    )

    input_hashes = {
        "run_manifest_sha256": sha256_file(run_manifest_path),
        "raw_memberships_sha256": sha256_file(raw_memberships_path),
        "reconciliation_report_sha256": sha256_file(
            reconciliation_report_path
        ),
        "review_manifest_sha256": sha256_file(review_manifest_path),
        "review_ledger_sha256": sha256_file(review_ledger_path),
        "historical_membership_acceptance_manifest_sha256": sha256_file(
            historical_membership_acceptance_manifest_path
        ),
        "historical_membership_acceptance_ledger_sha256": sha256_file(
            historical_membership_acceptance_ledger_path
        ),
        "relationships_manifest_sha256": sha256_file(
            relationships_manifest_path
        ),
        "relationships_ledger_sha256": sha256_file(
            relationships_ledger_path
        ),
    }
    rows = build_rows(
        review_rows,
        relationship_rows,
        input_hashes=input_hashes,
        membership_query_provenance=membership_query_provenance,
        historical_acceptance=historical_acceptance,
    )
    ledger_path = output_dir / "accepted_legislation_relationships.ndjson"
    write_ndjson(ledger_path, rows)
    ledger_hash = sha256_file(ledger_path)
    status_counts = Counter(row["relationship_status"] for row in rows)
    membership_counts = Counter(row["membership_status"] for row in rows)
    role_counts = Counter(row["role"] for row in rows)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "status": "complete",
        "generation_complete": True,
        "staged_only": True,
        "production_artifacts_written": False,
        "canonical_ledger_schema": SCHEMA,
        "canonical_ledger_file": ledger_path.name,
        "canonical_ledger_sha256": ledger_hash,
        "canonical_ledger_row_count": len(rows),
        "input_files": {
            "run_manifest": str(run_manifest_path),
            "raw_memberships": str(raw_memberships_path),
            "reconciliation_report": str(reconciliation_report_path),
            "review_manifest": str(review_manifest_path),
            "review_ledger": str(review_ledger_path),
            "historical_membership_acceptance_manifest": str(
                historical_membership_acceptance_manifest_path
            ),
            "historical_membership_acceptance_ledger": str(
                historical_membership_acceptance_ledger_path
            ),
            "relationships_manifest": str(relationships_manifest_path),
            "relationships_ledger": str(relationships_ledger_path),
        },
        "input_hashes": input_hashes,
        "upstream_acceptance_passed": True,
        "membership_unresolved_count": 0,
        "membership_partition": membership_partition,
        "historical_membership_acceptance": {
            "membership_count": len(historical_acceptance),
            "officially_ratified_scoring_count": sum(
                row.get("current_scoring_evidence_eligible") is True
                for row in historical_acceptance.values()
            ),
            "non_scoring_count": sum(
                row.get("current_scoring_evidence_eligible") is not True
                for row in historical_acceptance.values()
            ),
            "lossless_acceptance_gate_passed": True,
        },
        "relationship_unresolved_measure_count": 0,
        "decay_policy": {
            "name": "aggressive_congress_decay_1_0.66_0.33_0.16",
            "weights": {str(key): value for key, value in DECAY_WEIGHT.items()},
        },
        "measure_weight_policy": measure_weight_policy(),
        "active_relationship_deduplication": (
            "One strongest current role per measure/category/member; "
            "Sponsor > Original Cosponsor > Cosponsor."
        ),
        "withdrawn_relationships_are_audit_visible_and_zero_weight": True,
        "role_weight_policy_scenarios": ROLE_WEIGHT_POLICY_SCENARIOS,
        "selected_role_weight_policy": None,
        "role_weight_policy_reconciled": False,
        "promotion_ready": False,
        "promotion_blockers": ["role_weight_policy_unresolved"],
        "row_counts": {
            "relationship_status": dict(sorted(status_counts.items())),
            "membership_status": dict(sorted(membership_counts.items())),
            "role": dict(sorted(role_counts.items())),
            "scoring_eligible": sum(
                row["scoring_eligible"] for row in rows
            ),
        },
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    args = parse_args()
    try:
        manifest = build_generation(
            run_manifest_path=args.run_manifest,
            reconciliation_report_path=args.reconciliation_report,
            review_manifest_path=args.review_manifest,
            review_ledger_path=args.review_ledger,
            historical_membership_acceptance_manifest_path=(
                args.historical_membership_acceptance_manifest
            ),
            historical_membership_acceptance_ledger_path=(
                args.historical_membership_acceptance_ledger
            ),
            relationships_manifest_path=args.relationships_manifest,
            relationships_ledger_path=args.relationships_ledger,
            out_dir=args.out,
        )
    except (GenerationGateError, OSError, json.JSONDecodeError) as error:
        print(f"ACCEPTED GENERATION BLOCKED: {error}")
        return 2
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
