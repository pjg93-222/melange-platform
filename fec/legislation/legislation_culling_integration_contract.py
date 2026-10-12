"""Fail-closed contracts for legislation culling and staged integration.

This module is deliberately side-effect free.  It does not collect, cull,
score, synchronize, promote, or write production data.  It centralizes the
rules that turn immutable candidate evidence and membership adjudications into
a reversible keep/junk/flagged partition and a staged-integration gate.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

from collections import defaultdict
import re
from typing import Any, Iterable, Mapping, Sequence

try:
    from legislation_measure_key import CANONICAL_KEY_RE
except ImportError:  # pragma: no cover - package import path
    from fec.legislation_measure_key import CANONICAL_KEY_RE


EVIDENCE_SCHEMA = "acu-legislation-candidate-evidence-v1"
MEMBERSHIP_SCHEMA = "acu-legislation-membership-adjudication-v1"
PARTITION_SCHEMA = "acu-legislation-culling-partition-v1"
INTEGRATION_GATE_SCHEMA = "acu-legislation-corpus-integration-gate-v1"

FINAL_DISPOSITIONS = {"include", "exclude_false_positive"}
# One shared definition; the Congress window is the single named constant
# ``legislation_measure_key.LAST_SUPPORTED_CONGRESS``.
MEASURE_RE = CANONICAL_KEY_RE
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONGRESS_URL_RE = re.compile(r"^https://(?:www\.)?congress\.gov/", re.I)

REQUIRED_FULL_TEXT_CHECKS = (
    "status_200",
    "exact_url",
    "html_content_type",
    "not_challenge",
    "expected_main_marker",
)

REQUIRED_INTEGRATION_GATES = (
    "baseline_snapshot_verified",
    "discovery_complete",
    "congress_advanced_is_primary",
    "quorum_unused",
    "registry_complete",
    "discovery_reconciliation_exact",
    "quorum_floor_recall_audited",
    "identifier_probe_revalidation_complete",
    "legacy_snapshot_reaudit_complete",
    "full_text_coverage_complete",
    "full_text_failures_resolved",
    "resumed_full_text_archives_reverified",
    "all_exclusion_text_versions_reviewed",
    "semantic_parser_parity_passed",
    "amendment_evidence_closed",
    "candidate_evidence_partition_complete",
    "membership_adjudication_complete",
    "membership_partition_exact",
    "candidate_partition_exact",
    "junk_ledger_audit_passed",
    "relationship_roles_complete",
    "stage_actions_complete",
    "accepted_generation_hash_bound",
    "shadow_recompute_passed",
    "rank_drift_reviewed",
    "presync_audit_passed",
    "final_regression_hash_current",
)


class CullingContractError(ValueError):
    """Raised when evidence or adjudication violates the safety contract."""


def _nonempty(value: Any) -> bool:
    return bool(str(value or "").strip())


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CullingContractError(message)


def _valid_sha(value: Any) -> bool:
    return bool(SHA256_RE.fullmatch(str(value or "")))


def _measure_kind(canonical_key: str) -> str:
    match = MEASURE_RE.fullmatch(canonical_key)
    if not match:
        raise CullingContractError(f"invalid canonical_key: {canonical_key!r}")
    return match.group(2)


def _validate_source_receipt(
    value: Any,
    *,
    label: str,
    require_congress_url: bool = True,
) -> None:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    _require(_valid_sha(value.get("sha256")), f"{label}.sha256 is invalid")
    _require(_nonempty(value.get("path")), f"{label}.path is required")
    if require_congress_url:
        _require(
            bool(CONGRESS_URL_RE.match(str(value.get("source_url") or ""))),
            f"{label}.source_url must be Congress.gov",
        )


def validate_candidate_evidence(row: Mapping[str, Any]) -> dict[str, bool]:
    """Validate one immutable candidate evidence envelope.

    The returned readiness flags separate semantic review readiness from role
    and stage readiness so culling can proceed without accidentally authorizing
    downstream scoring.
    """

    _require(row.get("schema") == EVIDENCE_SCHEMA, "candidate evidence schema drift")
    canonical_key = str(row.get("canonical_key") or "")
    measure_kind = _measure_kind(canonical_key)
    _require(row.get("candidate_preserved") is True, "candidate is not preserved")

    discovery = row.get("discovery")
    _validate_source_receipt(
        discovery,
        label="discovery",
        require_congress_url=False,
    )
    _require(
        _valid_sha((discovery or {}).get("record_sha256")),
        "discovery.record_sha256 is invalid",
    )

    query_ids = row.get("membership_query_ids")
    _require(
        isinstance(query_ids, Sequence)
        and not isinstance(query_ids, (str, bytes))
        and bool(query_ids),
        "membership_query_ids must be nonempty",
    )
    normalized_query_ids = [str(value or "").strip() for value in query_ids]
    _require(all(normalized_query_ids), "membership_query_ids contains blanks")
    _require(
        len(normalized_query_ids) == len(set(normalized_query_ids)),
        "membership_query_ids contains duplicates",
    )

    official = row.get("official_evidence")
    _require(isinstance(official, Mapping), "official_evidence must be an object")
    title = official.get("title") or {}
    summary = official.get("summary") or {}
    full_text = official.get("full_text") or {}

    title_ready = bool(
        title.get("status") == "available"
        and _valid_sha(title.get("text_sha256"))
        and CONGRESS_URL_RE.match(str(title.get("source_url") or ""))
    )
    summary_ready = bool(
        summary.get("status") == "available"
        and _valid_sha(summary.get("text_sha256"))
        and CONGRESS_URL_RE.match(str(summary.get("source_url") or ""))
    )
    summary_explicitly_unavailable = bool(
        summary.get("status") == "officially_unavailable"
        and _nonempty(summary.get("unavailability_reason"))
        and _valid_sha(summary.get("availability_receipt_sha256"))
    )

    full_text_checks = full_text.get("validation") or {}
    full_text_ready = bool(
        full_text.get("status") in {"accepted", "resumed"}
        and _valid_sha(full_text.get("body_sha256"))
        and CONGRESS_URL_RE.match(str(full_text.get("expected_url") or ""))
        and full_text.get("expected_url") == full_text.get("final_url")
        and all(full_text_checks.get(name) is True for name in REQUIRED_FULL_TEXT_CHECKS)
        and full_text.get("cookie_values_persisted") is False
        and full_text.get("archive_reverified") is True
        and full_text.get("decompressed_body_sha256") == full_text.get("body_sha256")
        and _valid_sha(full_text.get("marker_bounded_text_sha256"))
        and _valid_sha(full_text.get("extraction_receipt_sha256"))
    )

    category_evidence = row.get("category_evidence") or []
    category_ids = [
        str(item.get("category_id") or "")
        for item in category_evidence
        if isinstance(item, Mapping)
    ]
    category_audit_ready = bool(
        isinstance(category_evidence, Sequence)
        and not isinstance(category_evidence, (str, bytes))
        and len(category_evidence) == 19
        and len(category_ids) == 19
        and len(set(category_ids)) == 19
        and all(category_ids)
        and all(
            item.get("title_reviewed") is True
            and item.get("summary_status_reviewed") is True
            and item.get("full_text_reviewed") is True
            and item.get("absence_is_neutral") is True
            and _valid_sha(item.get("evidence_sha256"))
            for item in category_evidence
        )
    )

    keyword_review = row.get("keyword_review") or {}
    keyword_review_ready = bool(
        keyword_review.get("ruleset_sha256")
        and _valid_sha(keyword_review.get("ruleset_sha256"))
        and keyword_review.get("title_reviewed") is True
        and keyword_review.get("summary_reviewed") is True
        and keyword_review.get("full_text_reviewed") is True
        and keyword_review.get("absence_is_neutral") is True
        and category_audit_ready
    )

    stage = row.get("stage") or {}
    stage_ready = bool(
        stage.get("status") == "available"
        and _nonempty(stage.get("value"))
        and _valid_sha(stage.get("actions_sha256"))
    )

    relationships = row.get("relationships") or {}
    relationship_required = measure_kind not in {"hamdt", "samdt"}
    relationship_ready = bool(
        not relationship_required
        or (
            relationships.get("status") == "complete"
            and relationships.get("role_provenance_complete") is True
            and int(relationships.get("sponsor_count") or 0) >= 1
            and int(relationships.get("original_cosponsor_count") or 0) >= 0
            and int(relationships.get("cosponsor_count") or 0) >= 0
            and _valid_sha(relationships.get("edges_sha256"))
        )
    )

    return {
        "title_ready": title_ready,
        "summary_ready": summary_ready,
        "summary_explicitly_unavailable": summary_explicitly_unavailable,
        "full_text_ready": full_text_ready,
        "keyword_review_ready": keyword_review_ready,
        "category_audit_ready": category_audit_ready,
        "semantic_review_ready": (
            title_ready
            and (summary_ready or summary_explicitly_unavailable)
            and full_text_ready
            and keyword_review_ready
        ),
        "stage_ready": stage_ready,
        "relationship_ready": relationship_ready,
        "integration_evidence_ready": (
            title_ready
            and (summary_ready or summary_explicitly_unavailable)
            and full_text_ready
            and keyword_review_ready
            and stage_ready
            and relationship_ready
        ),
    }


def membership_is_finalizable(
    row: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> bool:
    """Return whether a membership decision can leave the flagged queue."""

    if row.get("schema") != MEMBERSHIP_SCHEMA:
        return False
    if row.get("canonical_key") != evidence.get("canonical_key"):
        return False
    query_id = str(row.get("query_id") or "").strip()
    if not query_id or query_id not in set(evidence.get("membership_query_ids") or []):
        return False
    if row.get("disposition") not in FINAL_DISPOSITIONS:
        return False
    if row.get("adjudication_state") not in {
        "human_ratified",
        "reverified_preexisting_project_decision",
    }:
        return False
    if row.get("automated_final_decision") is not False:
        return False
    if row.get("reviewer_confidence") not in {"high", "medium"}:
        return False
    if not all(
        _nonempty(row.get(name))
        for name in ("reason", "reason_code", "reviewer", "reviewed_at")
    ):
        return False
    if not _valid_sha(row.get("decision_evidence_sha256")):
        return False
    sources = set(row.get("evidence_sources_checked") or [])
    if not {"official_title", "official_full_text"}.issubset(sources):
        return False
    try:
        readiness = validate_candidate_evidence(evidence)
    except CullingContractError:
        return False
    if not readiness["semantic_review_ready"]:
        return False
    if readiness["summary_ready"] and "official_crs_summary" not in sources:
        return False
    if (
        readiness["summary_explicitly_unavailable"]
        and "official_summary_unavailable" not in sources
    ):
        return False
    if row.get("disposition") == "exclude_false_positive":
        if row.get("reviewer_confidence") != "high":
            return False
        if row.get("absence_used_as_exclusion_evidence") is not False:
            return False
        if row.get("full_text_rule_out_complete") is not True:
            return False
        version_inventory = (
            (evidence.get("official_evidence") or {})
            .get("full_text", {})
            .get("version_inventory", {})
        )
        if (
            version_inventory.get("all_versions_reviewed") is not True
            or int(version_inventory.get("reviewed_version_count") or 0)
            != int(version_inventory.get("expected_version_count") or -1)
        ):
            return False
    return True


def partition_candidates(
    evidence_rows: Iterable[Mapping[str, Any]],
    membership_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive a lossless keep/junk/flagged view without deleting candidates."""

    evidence_by_key: dict[str, Mapping[str, Any]] = {}
    for row in evidence_rows:
        key = str(row.get("canonical_key") or "")
        _measure_kind(key)
        _require(key not in evidence_by_key, f"duplicate candidate evidence: {key}")
        evidence_by_key[key] = row

    membership_by_candidate: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    membership_keys: set[tuple[str, str]] = set()
    for row in membership_rows:
        key = str(row.get("canonical_key") or "")
        query_id = str(row.get("query_id") or "")
        natural_key = (key, query_id)
        _require(key in evidence_by_key, f"membership has no candidate evidence: {key}")
        _require(natural_key not in membership_keys, f"duplicate membership: {natural_key}")
        membership_keys.add(natural_key)
        membership_by_candidate[key].append(row)

    keep: list[str] = []
    junk: list[str] = []
    flagged: list[str] = []
    unresolved_memberships: list[dict[str, str]] = []

    for key in sorted(evidence_by_key):
        evidence = evidence_by_key[key]
        rows = membership_by_candidate.get(key, [])
        expected = set(evidence.get("membership_query_ids") or [])
        observed = {str(row.get("query_id") or "") for row in rows}
        _require(expected == observed, f"membership partition drift for {key}")
        finalizable = [membership_is_finalizable(row, evidence) for row in rows]
        for row, ready in zip(rows, finalizable):
            if not ready:
                unresolved_memberships.append(
                    {"canonical_key": key, "query_id": str(row.get("query_id") or "")}
                )
        if any(
            ready and row.get("disposition") == "include"
            for row, ready in zip(rows, finalizable)
        ):
            keep.append(key)
        elif rows and all(
            ready and row.get("disposition") == "exclude_false_positive"
            for row, ready in zip(rows, finalizable)
        ):
            junk.append(key)
        else:
            flagged.append(key)

    _require(
        len(keep) + len(junk) + len(flagged) == len(evidence_by_key),
        "candidate partition is not lossless",
    )
    return {
        "schema": PARTITION_SCHEMA,
        "candidate_count": len(evidence_by_key),
        "membership_count": len(membership_keys),
        "keep": keep,
        "junk": junk,
        "flagged": flagged,
        "unresolved_memberships": unresolved_memberships,
        "candidate_deletion_performed": False,
        "junk_is_reversible_view": True,
        "main_corpus_write_authorized": False,
    }


def evaluate_integration_gate(
    gates: Mapping[str, Any],
    *,
    candidate_count: int,
    membership_count: int,
) -> dict[str, Any]:
    """Evaluate the terminal staged-integration gate.

    Passing this gate only makes a hash-bound generation eligible for an
    explicit promotion step.  This function never performs that step.
    """

    normalized = {
        name: gates.get(name) is True for name in REQUIRED_INTEGRATION_GATES
    }
    blockers = [name for name, passed in normalized.items() if not passed]
    return {
        "schema": INTEGRATION_GATE_SCHEMA,
        "status": "PASS_STAGED_INTEGRATION_ELIGIBLE" if not blockers else "BLOCKED",
        "candidate_count": int(candidate_count),
        "membership_count": int(membership_count),
        "gates": normalized,
        "blockers": blockers,
        "staged_generation_eligible": not blockers,
        "automatic_promotion_allowed": False,
        "production_write_performed": False,
        "main_corpus_write_performed": False,
        "scoring_performed": False,
        "sync_or_promotion_performed": False,
    }
