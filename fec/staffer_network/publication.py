# -*- coding: utf-8 -*-
"""Deterministic immutable-generation publication and fail-closed loading."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

try:  # import works both with fec/ and repository root on sys.path
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover - namespace-package invocation
    from fec.lane_file_io import atomic_write_bytes

from .source_manifest import (
    canonical_json_bytes, sha256_bytes, sha256_file, validate_manifest_files,
)
from .schema_validation import SchemaValidationError, validate_instance
from .validate import assert_public_safe


POINTER_NAME = "staffer_network_current.json"
POINTER_SCHEMA_ID = "staffer-network-current-pointer-v1"
MANIFEST_SCHEMA_ID = "staffer-network-generation-manifest-v1"
PRIVACY_CLASS = "public-safe-derived"
RESTRICTED_SCHEMA_IDS = frozenset({
    "staffer-network-employment-adjudication-decision-v1",
    "staffer-network-employment-episode-v1",
    "staffer-network-employment-lookup-candidate-v1",
    "staffer-network-employment-lookup-receipt-v1",
    "staffer-network-legistorm-employment-capture-v1",
    "staffer-network-legistorm-employment-checkpoint-receipt-v1",
    "staffer-network-legistorm-employment-observation-v1",
    "staffer-network-lda-identity-candidate-v1",
    "staffer-network-lda-identity-candidate-receipt-v1",
    "staffer-network-lda-resume-authority-receipt-v1",
    "staffer-network-legacy-source-native-record-v1",
    "staffer-network-temporal-build-receipt-v1",
    "staffer-network-temporal-edge-v1",
    "staffer-network-temporal-path-v1",
})
SCHEMA_FILES = {
    "staffer-network-atomicity-receipt-v1": "atomicity_receipt.schema.json",
    "staffer-network-canonical-entity-v1": "canonical_entity.schema.json",
    "staffer-network-canonical-fact-v1": "canonical_fact.schema.json",
    "staffer-network-canonical-generation-receipt-v1": "canonical_generation_receipt.schema.json",
    "staffer-network-canonical-identity-link-v1": "canonical_identity_link.schema.json",
    "staffer-network-canonical-network-edge-v1": "canonical_network_edge.schema.json",
    "staffer-network-coverage-v1": "coverage.schema.json",
    "staffer-network-current-pointer-v1": "current_pointer.schema.json",
    "staffer-network-employment-adjudication-decision-v1": "employment_adjudication_decision.schema.json",
    "staffer-network-employment-episode-v1": "employment_episode.schema.json",
    "staffer-network-employment-lookup-candidate-v1": "employment_lookup_candidate.schema.json",
    "staffer-network-employment-lookup-receipt-v1": "employment_lookup_receipt.schema.json",
    "staffer-network-generation-manifest-v1": "generation_manifest.schema.json",
    "staffer-network-historic-member-receipt-v1": "historic_member_receipt.schema.json",
    "staffer-network-industry-edge-v1": "industry_edge.schema.json",
    "staffer-network-internal-analysis-authority-v1": "internal_analysis_authority.schema.json",
    "staffer-network-isolation-receipt-v1": "isolation_receipt.schema.json",
    "staffer-network-legistorm-employment-capture-v1": "legistorm_employment_capture.schema.json",
    "staffer-network-legistorm-employment-checkpoint-receipt-v1": "legistorm_employment_checkpoint_receipt.schema.json",
    "staffer-network-legistorm-employment-observation-v1": "legistorm_employment_observation.schema.json",
    "staffer-network-lda-reconciliation-receipt-v1": "lda_reconciliation_receipt.schema.json",
    "staffer-network-lda-anonymous-supersession-receipt-v1": "lda_anonymous_supersession_receipt.schema.json",
    "staffer-network-lda-composite-reconciliation-receipt-v1": "lda_composite_reconciliation_receipt.schema.json",
    "staffer-network-lda-lobbyist-assertion-v1": "lda_lobbyist_assertion.schema.json",
    "staffer-network-lda-organization-assertion-v1": "lda_organization_assertion.schema.json",
    "staffer-network-lda-resume-authority-receipt-v1": "lda_resume_authority_receipt.schema.json",
    "staffer-network-lda-activity-assertion-v1": "lda_activity_assertion.schema.json",
    "staffer-network-lda-covered-position-assertion-v1": "lda_covered_position_assertion.schema.json",
    "staffer-network-lda-industry-edge-v1": "lda_industry_edge.schema.json",
    "staffer-network-lda-source-native-corpus-receipt-v1": "lda_source_native_corpus_receipt.schema.json",
    "staffer-network-lda-identity-candidate-v1": "lda_identity_candidate.schema.json",
    "staffer-network-lda-identity-candidate-receipt-v1": "lda_identity_candidate_receipt.schema.json",
    "staffer-network-legacy-artifact-registry-v1": "legacy_artifact_registry.schema.json",
    "staffer-network-legacy-seed-receipt-v1": "legacy_seed_receipt.schema.json",
    "staffer-network-legacy-source-native-record-v1": "legacy_source_native_record.schema.json",
    "staffer-network-littlesis-profile-receipt-v1": "littlesis_profile_receipt.schema.json",
    "staffer-network-littlesis-entity-assertion-v1": "littlesis_entity_assertion.schema.json",
    "staffer-network-littlesis-relationship-assertion-v1": "littlesis_relationship_assertion.schema.json",
    "staffer-network-member-overlay-v1": "member_overlay.schema.json",
    "staffer-network-network-build-receipt-v1": "network_build_receipt.schema.json",
    "staffer-network-network-score-v1": "network_score.schema.json",
    "staffer-network-person-edge-v1": "person_edge.schema.json",
    "politico-directory-crawl-receipt-v1": "politico_crawl_receipt.schema.json",
    "politico-directory-normalization-receipt-v1": "politico_normalization_receipt.schema.json",
    "politico-directory-resolution-pilot-receipt-v1": "politico_resolution_pilot_receipt.schema.json",
    "staffer-network-politico-adjudication-framework-receipt-v1": "politico_adjudication_framework_receipt.schema.json",
    "staffer-network-politico-adjudication-results-receipt-v1": "politico_adjudication_results_receipt.schema.json",
    "staffer-network-privacy-license-receipt-v1": "privacy_license_receipt.schema.json",
    "staffer-network-source-manifest-v1": "source_manifest.schema.json",
    "staffer-network-source-native-corpus-receipt-v1": "source_native_corpus_receipt.schema.json",
    "staffer-network-source-native-corpus-determinism-receipt-v1": "source_native_corpus_determinism_receipt.schema.json",
    "staffer-network-source-native-corpus-validation-receipt-v1": "source_native_corpus_validation_receipt.schema.json",
    "staffer-network-temporal-build-receipt-v1": "temporal_build_receipt.schema.json",
    "staffer-network-temporal-edge-v1": "temporal_edge.schema.json",
    "staffer-network-temporal-path-v1": "temporal_path.schema.json",
}


class GenerationError(RuntimeError):
    pass


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def deterministic_gzip(body: bytes, *, compresslevel: int = 9) -> bytes:
    target = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=target, compresslevel=compresslevel, mtime=0) as stream:
        stream.write(body)
    return target.getvalue()


def canonical_ndjson_gzip(records: Iterable[Mapping[str, Any]]) -> tuple[bytes, int]:
    """Sort records by canonical bytes and return deterministic gzip content."""

    encoded: list[bytes] = []
    for record in records:
        assert_public_safe(record)
        encoded.append(canonical_json_bytes(record))
    encoded.sort()
    return deterministic_gzip(b"".join(encoded)), len(encoded)


def schema_path(schema_directory: Path, schema_id: str) -> Path:
    filename = SCHEMA_FILES.get(schema_id)
    if filename is None:
        raise GenerationError(f"unregistered schema_id: {schema_id}")
    candidate = schema_directory / filename
    if not candidate.is_file():
        raise GenerationError(f"registered schema is missing: {candidate}")
    return candidate


def _validate_record_semantics(value: Mapping[str, Any], schema_id: str) -> None:
    if schema_id == "staffer-network-legistorm-employment-observation-v1":
        raw_payload = {
            key: value[key]
            for key in (
                "source_system_id", "profile_id", "profile_url", "retrieved_at",
                "source_as_of", "profile_title_raw", "lda_source_person_id",
                "source_row_id", "raw_person_name", "raw_employer", "raw_title",
                "raw_start", "normalized_start", "raw_end", "normalized_end",
                "start_precision", "end_precision", "source_end_semantics",
                "asserted_current",
            )
        }
        expected = hashlib.sha256(canonical_json_bytes(raw_payload)).hexdigest()
        if value["source_record_sha256"] != expected:
            raise GenerationError("LegiStorm source-record digest mismatch")
        if value["observation_id"] != "legistorm:employment-observation:" + expected:
            raise GenerationError("LegiStorm observation ID is not content-bound")
        employment_digest = hashlib.sha256(
            f"{value['profile_id']}\0{value['source_row_id']}".encode("utf-8")
        ).hexdigest()
        if value["person_subject_id"] != f"legistorm:person:{value['profile_id']}":
            raise GenerationError("LegiStorm person subject ID drifted")
        if value["employment_key"] != "legistorm:employment-key:" + employment_digest:
            raise GenerationError("LegiStorm employment key drifted")
        if value["container_subject_id"] != "legistorm:container-candidate:" + employment_digest:
            raise GenerationError("LegiStorm container subject ID drifted")
        start = (value["start_earliest"], value["start_latest"])
        end = (value["end_earliest"], value["end_latest"])
        if (start[0] is None) != (start[1] is None) or (end[0] is None) != (end[1] is None):
            raise GenerationError("LegiStorm observation interval bounds are incomplete")
        if start[0] is not None and start[0] > start[1]:
            raise GenerationError("LegiStorm observation start bounds are inverted")
        if end[0] is not None and end[0] > end[1]:
            raise GenerationError("LegiStorm observation end bounds are inverted")
        if value["asserted_current"] != (value["source_end_semantics"] == "current"):
            raise GenerationError("LegiStorm current/end semantics disagree")
    elif schema_id == "staffer-network-employment-adjudication-decision-v1":
        core = {
            key: value[key]
            for key in (
                "decision_kind", "subject_id", "status", "resolved_person_id",
                "normalized_container_id", "container_kind", "member_bioguide_id",
                "organization_id", "reviewer_kind", "reviewer_id", "recorded_at",
                "rationale",
            )
        }
        expected = hashlib.sha256(canonical_json_bytes(core)).hexdigest()
        if value["decision_payload_sha256"] != expected:
            raise GenerationError("employment adjudication payload digest mismatch")
        if value["decision_id"] != "legistorm:adjudication:" + expected:
            raise GenerationError("employment adjudication ID is not content-bound")
        if value["decision_kind"] == "PERSON_IDENTITY":
            if any(value[key] is not None for key in (
                "normalized_container_id", "container_kind",
                "member_bioguide_id", "organization_id",
            )):
                raise GenerationError("person adjudication contains container outcome")
            if value["status"] == "ACCEPTED" and not value["resolved_person_id"]:
                raise GenerationError("accepted person adjudication lacks resolved ID")
        else:
            if value["resolved_person_id"] is not None or value["container_kind"] is None:
                raise GenerationError("container adjudication contains person outcome")
            if value["status"] == "ACCEPTED" and not value["normalized_container_id"]:
                raise GenerationError("accepted container adjudication lacks normalized ID")
            if value["container_kind"] in {"member_office", "committee", "leadership", "admin"} and not value["member_bioguide_id"]:
                raise GenerationError("Hill container adjudication lacks member Bioguide ID")
            if value["container_kind"] in {"registrant", "client_activity"} and not value["organization_id"]:
                raise GenerationError("industry container adjudication lacks organization ID")
        if value["status"] == "REVIEW_REQUIRED":
            if value["reviewer_kind"] != "UNASSIGNED" or value["reviewer_id"] is not None:
                raise GenerationError("review-required adjudication is assigned")
        elif value["reviewer_kind"] == "UNASSIGNED" or not value["reviewer_id"]:
            raise GenerationError("completed employment adjudication lacks reviewer")
    elif schema_id == "staffer-network-legistorm-employment-checkpoint-receipt-v1":
        expected_generation = hashlib.sha256(canonical_json_bytes({
            "method_version": value["method_version"],
            "source_evidence_digest": value["source_evidence_digest"],
            "decision_evidence_digest": value["decision_evidence_digest"],
        })).hexdigest()
        if value["generation_id"] != "legistorm-employment-" + expected_generation:
            raise GenerationError("LegiStorm checkpoint generation ID drifted")
        if value["episode_rows"] != value["employment_keys"]:
            raise GenerationError("LegiStorm checkpoint episode/key count drifted")
        if sum(value["episode_eligibility_counts"].values()) != value["episode_rows"]:
            raise GenerationError("LegiStorm checkpoint does not conserve episodes")
        if sum(
            sum(statuses.values())
            for statuses in value["decision_status_counts"].values()
        ) != value["decision_rows"]:
            raise GenerationError("LegiStorm checkpoint does not conserve decisions")
        if len({row["role"] for row in value["files"]}) != 3:
            raise GenerationError("LegiStorm checkpoint file roles are incomplete")
    elif schema_id == "staffer-network-employment-lookup-candidate-v1":
        digest = hashlib.sha256(
            (
                value["source_binding_sha256"] + "\0"
                + value["lda_source_person_id"]
            ).encode("utf-8")
        ).hexdigest()
        if value["candidate_id"] != "employment:lookup:" + digest:
            raise GenerationError("employment lookup candidate ID is not source-bound")
        positions = value["covered_position_values"]
        expected_priority = (
            "P0" if value["government_term_hit"] else ("P1" if positions else "P2")
        )
        if value["priority"] != expected_priority:
            raise GenerationError("employment lookup priority is inconsistent")
        if value["canonical_merge"] is not False:
            raise GenerationError("employment lookup candidate cannot merge identity")
        if value["identity_status"] != "REVIEW_REQUIRED":
            raise GenerationError("employment lookup candidate bypasses review")
    elif schema_id == "staffer-network-employment-lookup-receipt-v1":
        expected_binding = hashlib.sha256(canonical_json_bytes({
            "method_version": value["method_version"],
            "lda_source_receipt_sha256": value["lda_source_receipt_sha256"],
        })).hexdigest()
        if value["source_binding_sha256"] != expected_binding:
            raise GenerationError("employment lookup source binding drifted")
        counts = value["priority_counts"]
        if sum(counts.values()) != value["lda_lobbyists"]:
            raise GenerationError("employment lookup queue does not conserve lobbyists")
        if counts["P0"] + counts["P1"] != value["covered_position_people"]:
            raise GenerationError("employment lookup covered-position count drifted")
        if value["file"]["records"] != value["lda_lobbyists"]:
            raise GenerationError("employment lookup file count does not reconcile")
        if value["identity_matches_asserted"] != 0 or value["canonical_identity_merges"] != 0:
            raise GenerationError("employment lookup receipt asserts an identity result")
    elif schema_id == "staffer-network-employment-episode-v1":
        start = (value["start_earliest"], value["start_latest"])
        end = (value["end_earliest"], value["end_latest"])
        if (start[0] is None) != (start[1] is None) or (end[0] is None) != (end[1] is None):
            raise GenerationError("employment episode interval bounds are incomplete")
        if start[0] is not None and start[0] > start[1]:
            raise GenerationError("employment episode start bounds are inverted")
        if end[0] is not None and end[0] > end[1]:
            raise GenerationError("employment episode end bounds are inverted")
        if start[0] is not None and end[1] is not None and start[0] > end[1]:
            raise GenerationError("employment episode has no possible realization")
        expected = hashlib.sha256(
            "\0".join(sorted(value["support_observation_ids"])).encode("utf-8")
        ).hexdigest()
        if expected != value["support_digest"]:
            raise GenerationError("employment episode support digest mismatch")
        if value["container_kind"] in {"member_office", "committee", "leadership", "admin"}:
            if not value["member_bioguide_id"]:
                raise GenerationError("Hill employment episode lacks a member bioguide")
        if value["container_kind"] in {"registrant", "client_activity"}:
            if not value["organization_id"]:
                raise GenerationError("industry episode lacks an organization ID")
    elif schema_id == "staffer-network-temporal-path-v1":
        if value["person_id"] == value["other_person_id"]:
            raise GenerationError("temporal path self-loop")
        expected = hashlib.sha256(
            "\0".join(sorted(value["support_observation_ids"])).encode("utf-8")
        ).hexdigest()
        if expected != value["support_digest"]:
            raise GenerationError("temporal path support digest mismatch")
        if value["status"] != "GUARANTEED" and value["primary_weight"] != 0:
            raise GenerationError("non-guaranteed temporal path has primary weight")
        if value["predicate"] == "CO_TENURE":
            if value["other_person_id"] is None or value["member_bioguide_id"] is not None:
                raise GenerationError("co-tenure path endpoints are invalid")
            if value["guaranteed_days"] is None or value["possible_days"] is None:
                raise GenerationError("co-tenure path lacks duration bounds")
            if value["guaranteed_days"] > value["possible_days"]:
                raise GenerationError("co-tenure guaranteed duration exceeds possible")
        else:
            if value["other_person_id"] is not None or not value["member_bioguide_id"]:
                raise GenerationError("revolving path endpoints are invalid")
            if value["status"] == "INTERVAL_BLOCKED":
                if value["gap_min_days"] is not None or value["gap_max_days"] is not None:
                    raise GenerationError("interval-blocked revolving path has gap bounds")
                if value["primary_weight"] != 0 or value["possible_weight"] != 0:
                    raise GenerationError("interval-blocked revolving path has temporal weight")
            else:
                if value["gap_min_days"] is None or value["gap_max_days"] is None:
                    raise GenerationError("revolving path lacks gap bounds")
                if value["gap_min_days"] > value["gap_max_days"]:
                    raise GenerationError("revolving path gap bounds are inverted")
    elif schema_id == "staffer-network-temporal-edge-v1":
        if value["subject_id"] == value["object_id"]:
            raise GenerationError("temporal edge self-loop")
        if float(value["raw_weight"]) <= 0:
            raise GenerationError("temporal edge raw weight must be positive")
        expected = hashlib.sha256(
            "\0".join(sorted(value["support_path_ids"])).encode("utf-8")
        ).hexdigest()
        if expected != value["support_digest"]:
            raise GenerationError("temporal edge support digest mismatch")
        if value["predicate"] == "CO_TENURE":
            if value["contributing_person_count"] is not None:
                raise GenerationError("co-tenure edge carries a revolving person count")
            if value["guaranteed_days"] is None or value["possible_days"] is None:
                raise GenerationError("co-tenure edge lacks duration bounds")
        else:
            if value["contributing_person_count"] != value["raw_weight"]:
                raise GenerationError("revolving edge raw weight is not the person count")
            if value["organization_role"] == "registrant_employer" and value["predicate"] != "FORMER_STAFFER_EMPLOYED_BY_REGISTRANT":
                raise GenerationError("registrant employment role/predicate mismatch")
            if value["organization_role"] == "client_activity" and value["predicate"] != "FORMER_STAFFER_NAMED_ON_ACTIVITY_FOR_CLIENT":
                raise GenerationError("client activity role/predicate mismatch")
    elif schema_id == "staffer-network-temporal-build-receipt-v1":
        if value["eligible_episode_rows"] + value["ineligible_episode_rows"] != value["episode_rows"]:
            raise GenerationError("temporal receipt does not conserve episode rows")
        if sum(value["co_tenure_status_counts"].values()) != value["co_tenure_candidate_keys"]:
            raise GenerationError("temporal receipt does not conserve co-tenure keys")
        if sum(value["revolving_status_counts"].values()) != value["revolving_candidate_keys"]:
            raise GenerationError("temporal receipt does not conserve revolving keys")
        if len({row["role"] for row in value["files"]}) != 3:
            raise GenerationError("temporal receipt file roles are incomplete")
    elif schema_id == "staffer-network-canonical-fact-v1":
        if (value.get("object_entity_id") is None) == (value.get("literal") is None):
            raise GenerationError("canonical fact requires exactly one object form")
        if value.get("object_entity_id") == value.get("subject_entity_id"):
            raise GenerationError("canonical fact self-loop")
    elif schema_id == "staffer-network-canonical-network-edge-v1":
        if value.get("subject_entity_id") == value.get("object_entity_id"):
            raise GenerationError("canonical network edge self-loop")
        if float(value.get("raw_weight", 0.0)) <= 0.0:
            raise GenerationError("canonical network edge weight must be positive")
    elif schema_id == "staffer-network-person-edge-v1":
        subject, object_ = value["subject_person_id"], value["object_person_id"]
        if subject == object_:
            raise GenerationError("person edge self-loop")
        if value["directed"] is False and subject > object_:
            raise GenerationError("undirected person edge endpoints are not canonical")
    elif schema_id == "staffer-network-member-overlay-v1":
        left, right = value["member_a_bioguide"], value["member_b_bioguide"]
        if left >= right:
            raise GenerationError("member overlay endpoints must be distinct and canonical")
    elif schema_id in {
        "staffer-network-industry-edge-v1",
        "staffer-network-lda-industry-edge-v1",
    }:
        allowed = {
            "NAMED_ON_ACTIVITY": {("person", "lda_activity")},
            "FILED_BY": {("lda_activity", "registrant")},
            "FOR_CLIENT": {("lda_activity", "client")},
            "HILL_TO_LOBBYING_TRANSITION": {("person", "registrant"), ("person", "client")},
        }
        endpoints = (value["subject_kind"], value["object_kind"])
        if endpoints not in allowed[value["predicate"]]:
            raise GenerationError("industry predicate has invalid endpoint kinds")
        if value["subject_id"] == value["object_id"]:
            raise GenerationError("industry edge self-loop")
        if schema_id == "staffer-network-lda-industry-edge-v1":
            expected_prefixes = {
                "person": "lda:lobbyist:",
                "lda_activity": "lda:activity:",
                "registrant": "lda:registrant:",
                "client": "lda:client:",
            }
            if not value["subject_id"].startswith(
                expected_prefixes[value["subject_kind"]]
            ) or not value["object_id"].startswith(
                expected_prefixes[value["object_kind"]]
            ):
                raise GenerationError("LDA industry edge escapes source namespaces")
    elif schema_id == "staffer-network-coverage-v1":
        metrics = value["metrics"]
        metric_ids = [metric["metric_id"] for metric in metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise GenerationError("coverage metric IDs must be unique")
        for metric in metrics:
            provenance_state = metric["provenance_manifest_state"]
            manifest_hashes = metric["manifest_sha256"]
            if provenance_state == "SEALED" and not manifest_hashes:
                raise GenerationError("sealed metric provenance requires a manifest hash")
            if provenance_state == "LEGACY_UNMANIFESTED" and manifest_hashes:
                raise GenerationError("legacy-unmanifested metric provenance cannot claim manifest hashes")
            denominator = metric["eligible_denominator_count"]
            numerator = metric["numerator_count"]
            if numerator > denominator:
                raise GenerationError("coverage numerator exceeds denominator")
            if metric["result"] == "PASS" and denominator == 0:
                raise GenerationError("coverage PASS cannot have a zero denominator")
            if metric["result"] == "PASS" and provenance_state != "SEALED":
                raise GenerationError("coverage PASS requires sealed provenance")
            if metric["result"] == "NOT_EVALUABLE" and not metric["not_evaluable_reason"]:
                raise GenerationError("NOT_EVALUABLE requires a reason")
            if metric["result"] != "NOT_EVALUABLE" and metric["not_evaluable_reason"] is not None:
                raise GenerationError("only NOT_EVALUABLE may carry a not-evaluable reason")
            interval = metric["confidence_interval"]
            if interval is not None and interval["lower"] > interval["upper"]:
                raise GenerationError("coverage confidence interval is inverted")
            if metric["result"] in {"PASS", "FAIL"}:
                statistic = metric["statistic"]
                threshold = metric["threshold"]
                comparison = metric["comparison"]
                if statistic is None or threshold is None or comparison is None:
                    raise GenerationError("coverage PASS/FAIL requires statistic, threshold, and comparison")
                met = {
                    ">=": statistic >= threshold,
                    "<=": statistic <= threshold,
                    "=": statistic == threshold,
                }[comparison]
                if (metric["result"] == "PASS") != met:
                    raise GenerationError("coverage result contradicts statistic and threshold")
    elif schema_id == "politico-directory-crawl-receipt-v1":
        retained_fields = {
            "issue_areas", "name", "partition", "partition_ordinal", "profile_uuid", "roles"
        }
        expected_partitions = value["partition_expected"]
        observed_partitions = value["partition_observed"]
        if sum(expected_partitions.values()) != value["source_census"]:
            raise GenerationError("Politico partition inventory does not sum to source census")
        if observed_partitions != expected_partitions:
            raise GenerationError("Politico partition observations do not reconcile")
        if value["observed_rows"] != value["source_census"]:
            raise GenerationError("Politico observed rows do not reconcile to census")
        if value["unique_profile_uuids"] + value["duplicate_uuid_occurrences"] != value["observed_rows"]:
            raise GenerationError("Politico UUID conservation is inconsistent")
        if value["pages_observed"] != value["pages_expected"]:
            raise GenerationError("Politico page inventory is incomplete")
        if value["max_requests_in_any_rolling_minute"] > 60:
            raise GenerationError("Politico provisional rate envelope was exceeded")
        if value["max_in_flight_requests"] > 4:
            raise GenerationError("Politico concurrency envelope was exceeded")
        if set(value["retained_fields"]) != retained_fields:
            raise GenerationError("Politico retained fields are not exactly allowlisted")
        if value["status"] == "PASS" and (
            value["request_failures"] != 0
            or value["duplicate_uuid_occurrences"] != 0
            or value["prohibited_contact_keys"]
            or value["rows_matching_email_or_phone_patterns"] != 0
            or value["failures"]
        ):
            raise GenerationError("Politico PASS receipt contains failing outcomes")
    elif schema_id == "staffer-network-isolation-receipt-v1":
        if value["protected_outputs_before"] != value["protected_outputs_after"]:
            raise GenerationError("I-10a protected output inventories differ")
        if value["status"] == "PASS" and (
            value["unexpected_reader_paths"]
            or value["missing_approved_reader_paths"]
            or value["off_open_attempts"] != 0
            or value["off_results_null"] is not True
            or value["physical_absence_result_null"] is not True
            or value["protected_output_changes"]
            or value["failures"]
        ):
            raise GenerationError("I-10a PASS receipt contains failing outcomes")
    elif schema_id == "staffer-network-lda-anonymous-supersession-receipt-v1":
        if value["legacy_inventory_before_sha256"] != value["legacy_inventory_after_sha256"]:
            raise GenerationError("LDA supersession changed the sealed legacy inventory")
        prefix = value["corroborating_prefix"]
        if prefix["filing_objects"] != prefix["unique_filing_uuids"]:
            raise GenerationError("LDA supersession prefix is not UUID-unique")
    elif schema_id == "staffer-network-lda-composite-reconciliation-receipt-v1":
        before = value["legacy_inventory_before"]["inventory_sha256"]
        after = value["legacy_inventory_after"]["inventory_sha256"]
        if before != after:
            raise GenerationError("LDA composite changed the sealed legacy inventory")
        if value["legacy_ordering_audit"]["pass"] is not True:
            raise GenerationError("LDA composite lacks the offline ordering proof")
        if value["legacy_ordering_audit"]["page_shape_pass"] is not True:
            raise GenerationError("LDA composite legacy page shapes are invalid")
        if (
            value["base_page_size"] != 25
            or value["max_in_flight_requests"] != 8
            or value["transport_mode"]
            != "BOUNDED_BATCH_ASCENDING_PROCESSING"
        ):
            raise GenerationError("LDA composite transport contract is invalid")
        coverage = value["legacy_split_cohort_coverage"]
        if (
            coverage["pass"] is not True
            or coverage["uncovered_by_year"]
            or coverage["localized_split_timestamps"]
            != coverage["covered_split_timestamps"]
        ):
            raise GenerationError("LDA composite split-cohort coverage is incomplete")
        if value["filter_semantics"]["pass"] is not True:
            raise GenerationError("LDA composite lacks a passing filter probe")
        for window in value["windows"]:
            comparison = window["comparison"]
            if not (
                comparison["count_a"] == comparison["count_b"]
                and comparison["uuid_inventory_sha256_a"]
                == comparison["uuid_inventory_sha256_b"]
                and comparison["payload_inventory_sha256_a"]
                == comparison["payload_inventory_sha256_b"]
            ):
                raise GenerationError("LDA composite twin inventories differ")
        if sum(row["composite_unique_filing_uuids"] for row in value["per_year"]) != value["global_unique_filing_uuids"]:
            raise GenerationError("LDA composite global UUID conservation differs")
        resume = value["resume"]
        if resume["resumed"] != bool(resume["events"]):
            raise GenerationError("LDA composite resume marker disagrees with events")
        seen_failed_statuses = set()
        for event in resume["events"]:
            failed_status = event["failed_status_sha256"]
            if failed_status in seen_failed_statuses:
                raise GenerationError("LDA composite repeats a failed resume status")
            seen_failed_statuses.add(failed_status)
            expected_path = f"resume_authority_receipt_{failed_status}.json"
            if event["authority_receipt"]["path"] != expected_path:
                raise GenerationError("LDA composite resume authority path is not hash-bound")
    elif schema_id == "staffer-network-lda-lobbyist-assertion-v1":
        if value["source_person_id"] != f"lda:lobbyist:{value['source_numeric_id']}":
            raise GenerationError("LDA lobbyist source ID does not match numeric ID")
        if value["display_name"] not in value["name_variants"]:
            raise GenerationError("LDA lobbyist display name is not an observed variant")
        if value["covered_position_support_count"] == 0 and value["covered_position_values"]:
            raise GenerationError("LDA lobbyist has values without covered-position support")
    elif schema_id == "staffer-network-lda-organization-assertion-v1":
        expected = f"lda:{value['organization_kind']}:{value['source_numeric_id']}"
        if value["source_org_id"] != expected:
            raise GenerationError("LDA organization source ID does not match kind/numeric ID")
        if value["display_name"] not in value["name_variants"]:
            raise GenerationError("LDA organization display name is not an observed variant")
    elif schema_id == "staffer-network-lda-activity-assertion-v1":
        suffix = f":{value['filing_uuid']}:{value['activity_ordinal']}"
        if not value["source_activity_id"].endswith(suffix):
            raise GenerationError("LDA activity source ID does not match filing/ordinal")
    elif schema_id == "staffer-network-lda-covered-position-assertion-v1":
        if value["source_person_id"] == value["source_activity_id"]:
            raise GenerationError("LDA covered-position assertion has a self reference")
    elif schema_id == "staffer-network-lda-source-native-corpus-receipt-v1":
        counts = {row["role"]: row["records"] for row in value["files"]}
        if len(counts) != len(value["files"]):
            raise GenerationError("LDA source-native corpus file roles are duplicated")
        expected = {
            "lobbyist_assertions": value["lobbyists"],
            "organization_assertions": value["registrants"] + value["clients"],
            "activity_assertions": value["activities"],
            "industry_edges": value["industry_edges"],
            "covered_position_assertions": value["covered_position_assertions"],
        }
        if counts != expected:
            raise GenerationError("LDA source-native corpus file counts do not conserve")
        schema_by_role = {row["role"]: row["schema_id"] for row in value["files"]}
        expected_schemas = {
            "lobbyist_assertions": "staffer-network-lda-lobbyist-assertion-v1",
            "organization_assertions": "staffer-network-lda-organization-assertion-v1",
            "activity_assertions": "staffer-network-lda-activity-assertion-v1",
            "industry_edges": "staffer-network-lda-industry-edge-v1",
            "covered_position_assertions": "staffer-network-lda-covered-position-assertion-v1",
        }
        if schema_by_role != expected_schemas:
            raise GenerationError("LDA source-native corpus role/schema binding mismatch")
    elif schema_id == "staffer-network-lda-identity-candidate-v1":
        digest = hashlib.sha256(
            (
                value["source_binding_sha256"] + "\0"
                + value["method_version"] + "\0"
                + value["lda_source_person_id"] + "\0"
                + value["matched_source_system_id"] + "\0"
                + value["matched_source_entity_id"]
            ).encode("utf-8")
        ).hexdigest()
        if value["candidate_id"] != "lda:identity-candidate:" + digest:
            raise GenerationError("LDA identity candidate ID is not source-bound")
        if value["review_status"] != "REVIEW_REQUIRED" or value["canonical_merge"]:
            raise GenerationError("LDA identity candidate bypasses review")
        if value["confidence"] is not None:
            raise GenerationError("LDA identity candidate emits a confidence score")
    elif schema_id == "staffer-network-lda-identity-candidate-receipt-v1":
        if not (
            value["candidate_count"]
            == value["file"]["records"]
            == value["review_required_count"]
            == sum(value["candidate_count_by_source"].values())
        ):
            raise GenerationError("LDA identity candidate counts do not conserve")
        if any(
            value["unique_lda_lobbyists_by_source"].get(source, 0) > count
            for source, count in value["candidate_count_by_source"].items()
        ):
            raise GenerationError("LDA candidate unique-lobbyist count exceeds rows")
        if (
            value["candidates_with_government_term_hit"] > value["candidate_count"]
            or value["candidates_with_any_name_collision"] > value["candidate_count"]
            or value["candidates_with_cross_source_name_multiplicity"]
            > value["candidate_count"]
            or value["confidence_values_emitted"] != 0
            or value["canonical_identity_merges"] != 0
            or value["downstream_identity_gate"] != "CLOSED"
            or value["failures"]
        ):
            raise GenerationError("LDA identity candidate PASS receipt is unsafe")
    elif schema_id == "staffer-network-atomicity-receipt-v1":
        if value["logical_table_count"] != value["nonempty_table_count"]:
            raise GenerationError("I-11 logical database fixture is vacuous")
        if not (
            value["clean_build_digest_a"]
            == value["clean_build_digest_b"]
            == value["incremental_build_digest"]
        ):
            raise GenerationError("I-11 logical database digests differ")
        if value["status"] == "PASS" and (
            value["clean_builds_equal"] is not True
            or value["clean_incremental_equal"] is not True
            or value["clean_integrity_check"] != "ok"
            or value["incremental_integrity_check"] != "ok"
            or value["fault_injection_raised"] is not True
            or value["pointer_unchanged_after_fault"] is not True
            or value["reader_old_after_fault"] is not True
            or value["reader_new_after_success"] is not True
            or value["mixed_generation_observations"] != 0
            or value["failures"]
        ):
            raise GenerationError("I-11 PASS receipt contains failing outcomes")
    elif schema_id == "staffer-network-privacy-license-receipt-v1":
        if value["source_manifest_count"] != len(value["source_manifests"]):
            raise GenerationError("P-1 source manifest count is inconsistent")
        if value["staff_rows_scanned"] != sum(
            artifact["staff_rows"] for artifact in value["staff_artifacts"]
        ):
            raise GenerationError("P-1 staff-row count is inconsistent")
        if value["status"] == "PASS" and (
            value["prohibited_field_findings"] != 0
            or value["contact_pattern_rows"] != 0
            or value["unexpected_staff_fields"]
            or value["manifest_policy_violations"]
            or value["restricted_raw_leak_paths"]
            or value["legacy_original_backup_verified"] is not True
            or value["legacy_current_scrubbed"] is not True
            or value["failures"]
        ):
            raise GenerationError("P-1 PASS receipt contains failing outcomes")
    elif schema_id == "staffer-network-legacy-artifact-registry-v1":
        for artifact in value["artifacts"]:
            if artifact["contact_keys_removed"] != artifact["original_rows"] * 2:
                raise GenerationError("legacy contact removal count is inconsistent")
            if artifact["remediated_contact_key_count"] != 0:
                raise GenerationError("legacy registry claims residual contact keys")
            if artifact["canonical_staffer_network_use"] is not False:
                raise GenerationError("legacy quarantined source cannot be canonical")
    elif schema_id == "politico-directory-normalization-receipt-v1":
        if len(value["files"]) != 3:
            raise GenerationError("Politico normalized file inventory must contain three files")
        advertised = {entry["path"]: entry["records"] for entry in value["files"]}
        expected = {
            "persons.ndjson": value["normalized_persons"],
            "role_assertions.ndjson": value["role_assertions"],
            "issue_assertions.ndjson": value["issue_assertions"],
        }
        if advertised != expected:
            raise GenerationError("Politico normalized file inventory is inconsistent")
        expected_fields = {
            "person_fields": {
                "display_name", "name_normalized", "observed_at", "partition_ordinal",
                "party_partition", "source_person_id", "source_profile_uuid",
            },
            "role_fields": {
                "assertion_id", "observed_at", "predicate", "role_ordinal", "role_text",
                "source_person_id", "source_profile_uuid",
            },
            "issue_fields": {
                "assertion_id", "issue_area", "issue_ordinal", "observed_at", "predicate",
                "source_person_id", "source_profile_uuid",
            },
        }
        if any(set(value[key]) != fields for key, fields in expected_fields.items()):
            raise GenerationError("Politico normalized field allowlists are inconsistent")
        if value["source_profiles"] != value["normalized_persons"]:
            raise GenerationError("Politico normalized person conservation failed")
        if value["status"] == "PASS" and (
            value["contact_findings"] != 0
            or value["canonical_entity_merges"] != 0
            or value["failures"]
        ):
            raise GenerationError("Politico normalization PASS contains failing outcomes")
    elif schema_id == "politico-directory-resolution-pilot-receipt-v1":
        dispositions = value["dispositions"]
        legacy_outcomes = value["legacy_outcomes"]
        if value["candidate_count"] != sum(
            count for disposition, count in dispositions.items()
            if disposition != "REVIEW_NAME_COLLISION"
        ):
            raise GenerationError("Politico resolution candidate count is inconsistent")
        if (
            value["strict_candidate_count"]
            != dispositions["STRICT_CANDIDATE_NAME_TITLE_OFFICE"]
        ):
            raise GenerationError("Politico strict-candidate count is inconsistent")
        if sum(legacy_outcomes.values()) != value["legacy_staff_rows"]:
            raise GenerationError("Politico legacy outcome conservation failed")
        if value["exact_name_overlap_profiles"] > value["politico_persons"]:
            raise GenerationError("Politico profile overlap exceeds the profile census")
        if value["exact_name_overlap_legacy_rows"] > value["legacy_staff_rows"]:
            raise GenerationError("Politico legacy overlap exceeds the legacy census")
        if legacy_outcomes["PENDING_UNIQUE_NAME_CANDIDATE"] != value["candidate_count"]:
            raise GenerationError("Politico candidate outcome count is inconsistent")
        if (
            value["exact_name_overlap_legacy_rows"]
            != value["candidate_count"] + legacy_outcomes["REVIEW_NAME_COLLISION"]
        ):
            raise GenerationError("Politico exact-overlap outcome count is inconsistent")
        if value["review_sample_count"] != min(
            value["strict_candidate_count"], value["review_sample_requested"]
        ):
            raise GenerationError("Politico resolution sample count is inconsistent")
        adjudicated = value["adjudicated_sample_count"]
        precision = value["estimated_precision"]
        if adjudicated > value["review_sample_count"]:
            raise GenerationError("Politico adjudicated count exceeds the review sample")
        if (adjudicated == 0) != (precision is None):
            raise GenerationError("Politico precision requires a nonempty adjudicated sample")
        if precision is not None and not 0.0 <= precision <= 1.0:
            raise GenerationError("Politico estimated precision is outside [0,1]")
        if value["status"] == "REPORT" and (
            value["canonical_entity_merges"] != 0
            or value["auto_merge_enabled"] is not False
            or value["adjudication_required"] is not True
            or value["failures"]
        ):
            raise GenerationError("Politico resolution REPORT violates no-merge controls")
    elif schema_id == "staffer-network-politico-adjudication-framework-receipt-v1":
        sample_count = value["review_sample_count"]
        if value["calibration_count"] + value["holdout_count"] != sample_count:
            raise GenerationError("adjudication split does not conserve the sample")
        reviewers = value["reviewer_ids"]
        if len(reviewers) != 2 or len(set(reviewers)) != 2:
            raise GenerationError("adjudication requires two distinct reviewers")
        if value["review_assignments"] != sample_count * len(reviewers):
            raise GenerationError("adjudication assignment count is inconsistent")
        packet_files = value["packet_files"]
        packet_keys = {
            (entry["reviewer_id"], entry["partition"]) for entry in packet_files
        }
        expected_packet_keys = {
            (reviewer, partition)
            for reviewer in reviewers
            for partition in ("CALIBRATION", "HOLDOUT")
        }
        if packet_keys != expected_packet_keys:
            raise GenerationError("adjudication packet inventory is incomplete")
        split_counts = {
            "CALIBRATION": value["calibration_count"],
            "HOLDOUT": value["holdout_count"],
        }
        if any(entry["records"] != split_counts[entry["partition"]] for entry in packet_files):
            raise GenerationError("adjudication packet count differs from split seal")
        forbidden = {
            "disposition", "title_match", "office_evidence_available",
            "office_match", "review_status",
        }
        if set(value["excluded_machine_fields"]) != forbidden:
            raise GenerationError("adjudication excluded-field inventory drifted")
        if forbidden & set(value["review_packet_fields"]):
            raise GenerationError("adjudication reviewer packet exposes machine fields")
        if value["status"] == "REPORT" and (
            value["source_authorization_status"] != "AUTHORIZED"
            or value["source_canonical_use_enabled"] is not False
            or value["submitted_label_count"] != 0
            or value["adjudicated_candidate_count"] != 0
            or value["estimated_precision"] is not None
            or value["canonical_entity_merges"] != 0
            or value["auto_merge_enabled"] is not False
            or value["failures"]
        ):
            raise GenerationError("adjudication framework REPORT violates pre-label controls")
    elif schema_id == "staffer-network-politico-adjudication-results-receipt-v1":
        reviewers = value["reviewer_ids"]
        label_files = value["label_files"]
        if {entry["reviewer_id"] for entry in label_files} != set(reviewers):
            raise GenerationError("adjudication result label-file inventory is incomplete")
        if sum(entry["records"] for entry in label_files) != value["submitted_label_count"]:
            raise GenerationError("adjudication result label count is inconsistent")
        if value["submitted_label_count"] != value["reviewed_candidate_count"] * len(reviewers):
            raise GenerationError("adjudication result reviewer coverage is incomplete")
        calibration = value["calibration"]
        holdout = value["holdout"]
        if calibration["candidate_count"] + holdout["candidate_count"] != value["reviewed_candidate_count"]:
            raise GenerationError("adjudication result split does not conserve candidates")
        for reviewer in reviewers:
            if sum(calibration["reviewer_outcome_counts"][reviewer].values()) != calibration["candidate_count"]:
                raise GenerationError("adjudication calibration outcome counts are inconsistent")
            if holdout["reviewer_label_counts"][reviewer] != holdout["candidate_count"]:
                raise GenerationError("adjudication holdout reviewer coverage is incomplete")
        if calibration["exact_agreement_count"] > calibration["candidate_count"]:
            raise GenerationError("adjudication exact agreement exceeds calibration count")
        if calibration["disagreement_queue_count"] != calibration["candidate_count"] - calibration["exact_agreement_count"]:
            raise GenerationError("adjudication disagreement queue count is inconsistent")
        if calibration["possible_or_abstain_pair_count"] != calibration["candidate_count"] - calibration["determinate_pair_count"]:
            raise GenerationError("adjudication indeterminate-pair count is inconsistent")
        if calibration["determinate_pair_count"] != calibration["determinate_agreement_count"] + calibration["conflicting_determinate_count"]:
            raise GenerationError("adjudication determinate-pair decomposition is inconsistent")
        if calibration["determinate_agreement_count"] != calibration["consensus_match_count"] + calibration["consensus_distinct_count"]:
            raise GenerationError("adjudication determinate agreement decomposition is inconsistent")
        expected_exact_rate = calibration["exact_agreement_count"] / calibration["candidate_count"]
        if abs(calibration["exact_agreement_rate"] - expected_exact_rate) > 1e-12:
            raise GenerationError("adjudication exact-agreement rate is inconsistent")
        determinate_count = calibration["determinate_pair_count"]
        expected_determinate_rate = (
            calibration["determinate_agreement_count"] / determinate_count
            if determinate_count else None
        )
        if calibration["determinate_agreement_rate"] != expected_determinate_rate:
            raise GenerationError("adjudication determinate-agreement rate is inconsistent")
        consensus_count = calibration["consensus_match_count"] + calibration["consensus_distinct_count"]
        expected_match_rate = (
            calibration["consensus_match_count"] / consensus_count
            if consensus_count else None
        )
        if calibration["consensus_match_rate"] != expected_match_rate:
            raise GenerationError("adjudication consensus match rate is inconsistent")
        if value["status"] == "REPORT" and (
            value["reviewer_type"] != "MODEL_ASSISTED"
            or value["human_gold_adjudicated_count"] != 0
            or value["estimated_human_gold_precision"] is not None
            or value["model_assisted_only"] is not True
            or holdout["status"] != "QUARANTINED"
            or holdout["outcome_counts_published"] is not False
            or holdout["evaluation_count"] != 0
            or value["canonical_entity_merges"] != 0
            or value["auto_merge_enabled"] is not False
            or value["failures"]
        ):
            raise GenerationError("adjudication results REPORT violates model-only quarantine controls")
    elif schema_id == "staffer-network-legacy-seed-receipt-v1":
        if value["observed_rows"] != value["accepted_rows"] + value["rejected_rows"]:
            raise GenerationError("legacy seed row conservation failed")
        if value["record_file"]["records"] != value["accepted_rows"]:
            raise GenerationError("legacy seed record-file count is inconsistent")
        if (
            value["status"] != "PASS"
            or value["accepted_rows"] != 5773
            or value["represented_offices"] != 533
            or value["contact_fields_retained"] != 0
            or value["source_native_ingest_enabled"] is not True
            or value["analysis_enabled"] is not False
            or value["canonical_use_enabled"] is not False
            or value["canonical_entity_merges"] != 0
            or value["redistribution_class"] != "restricted-local-only"
            or value["raw_quorum_workbooks_authorized"] is not False
            or value["failures"]
        ):
            raise GenerationError("legacy seed receipt violates restricted ingest controls")
    elif schema_id == "staffer-network-littlesis-profile-receipt-v1":
        roles = [entry["role"] for entry in value["files"]]
        if sorted(roles) != ["entities", "relationships"]:
            raise GenerationError("LittleSis file-role inventory is invalid")
        if value["entity_observed"] != (
            value["entity_accepted"] + value["entity_invalid"]
            + value["entity_duplicates"]
        ):
            raise GenerationError("LittleSis entity conservation failed")
        if value["relationship_observed"] != (
            value["relationship_accepted"] + value["relationship_rejected"]
        ):
            raise GenerationError("LittleSis relationship conservation failed")
        if value["relationship_rejected"] != (
            value["relationship_invalid"] + value["relationship_duplicates"]
            + value["relationship_dangling_rejected"]
        ):
            raise GenerationError("LittleSis rejection decomposition failed")
        if sum(value["category_observed"].values()) != (
            value["relationship_observed"] - value["relationship_invalid"]
        ):
            raise GenerationError("LittleSis observed category counts are inconsistent")
        if sum(value["category_accepted"].values()) != value["relationship_accepted"]:
            raise GenerationError("LittleSis accepted category counts are inconsistent")
        if value["undated_position_accepted"] > value["category_accepted"].get("Position", 0):
            raise GenerationError("LittleSis undated Position count exceeds accepted Positions")
        if value["status"] == "PASS" and (
            value["failures"]
            or value["source_native_ready"] is not True
            or value["relationship_matches_prior_dump"] is not True
            or value["accepted_dangling_relationships"] != 0
            or value["source_enabled"] is not False
            or value["canonical_entity_merges"] != 0
            or value["share_alike_publication_review_deferred"] is not True
        ):
            raise GenerationError("LittleSis PASS violates source-native quarantine controls")
    elif schema_id == "staffer-network-littlesis-entity-assertion-v1":
        expected_id = f"littlesis:entity:{value['source_numeric_id']}"
        if value["source_entity_id"] != expected_id:
            raise GenerationError("LittleSis entity assertion ID is inconsistent")
        parent = value["parent_source_entity_id"]
        if parent is not None and not parent.startswith("littlesis:entity:"):
            raise GenerationError("LittleSis parent crosses the source namespace")
    elif schema_id == "staffer-network-littlesis-relationship-assertion-v1":
        expected_id = f"littlesis:relationship:{value['source_numeric_id']}"
        category_names = {1: "Position", 2: "Education", 7: "Lobbying"}
        if value["source_relationship_id"] != expected_id:
            raise GenerationError("LittleSis relationship assertion ID is inconsistent")
        if value["category_name"] != category_names[value["category_id"]]:
            raise GenerationError("LittleSis relationship category vocabulary drifted")
        if not value["subject_source_entity_id"].startswith("littlesis:entity:"):
            raise GenerationError("LittleSis relationship subject crosses its namespace")
        if not value["object_source_entity_id"].startswith("littlesis:entity:"):
            raise GenerationError("LittleSis relationship object crosses its namespace")
        if value["temporal_weight"] != 0:
            raise GenerationError("source-native LittleSis assertion carries temporal weight")
        boss = value["source_category_attributes"].get("boss_source_entity_id")
        if boss is not None and not (
            isinstance(boss, str) and re.fullmatch(r"littlesis:entity:[1-9][0-9]*", boss)
        ):
            raise GenerationError("LittleSis boss reference crosses its source namespace")
    elif schema_id == "staffer-network-source-native-corpus-receipt-v1":
        input_roles = {row["role"]: row for row in value["input_files"]}
        expected_input_roles = {"entities", "relationships"}
        if set(input_roles) != expected_input_roles:
            raise GenerationError("source-native corpus input inventory is incomplete")
        record_roles = {row["role"]: row for row in value["record_files"]}
        expected_record_roles = {
            "littlesis-entities", "littlesis-relationship-assertions",
            "quorum-legacy-source-native-seed",
        }
        if set(record_roles) != expected_record_roles:
            raise GenerationError("source-native corpus record inventory is incomplete")
        if value["entities_emitted"] != (
            value["entity_observed"] - value["entity_invalid"] - value["entity_duplicates"]
        ):
            raise GenerationError("source-native corpus entity conservation failed")
        rejected = (
            value["relationship_dangling_rejected"] + value["relationship_invalid"]
            + value["relationship_duplicates"]
        )
        if value["relationship_observed"] != (
            value["relationship_emitted_in_scope"]
            + value["relationship_out_of_scope_category"] + rejected
        ):
            raise GenerationError("source-native corpus relationship conservation failed")
        if sum(value["category_emitted"].values()) != value["relationship_emitted_in_scope"]:
            raise GenerationError("source-native corpus emitted categories do not conserve")
        if sum(value["category_out_of_scope"].values()) != value["relationship_out_of_scope_category"]:
            raise GenerationError("source-native corpus dispositions do not conserve")
        if set(value["category_emitted"]) != {"Position", "Education", "Lobbying"}:
            raise GenerationError("source-native corpus emitted category scope drifted")
        if value["category_vocabulary"].get("5") != "Donation" or value["category_vocabulary"].get("7") != "Lobbying":
            raise GenerationError("source-native corpus category correction is absent")
        if len(value["dangling_relationship_ids"]) != value["relationship_dangling_rejected"]:
            raise GenerationError("source-native corpus dangling-ID inventory is inconsistent")
        if record_roles["littlesis-entities"]["records"] != value["entities_emitted"]:
            raise GenerationError("source-native corpus entity file count is inconsistent")
        if record_roles["littlesis-relationship-assertions"]["records"] != value["relationship_emitted_in_scope"]:
            raise GenerationError("source-native corpus relationship file count is inconsistent")
        if value["undated_position_emitted"] > value["category_emitted"]["Position"]:
            raise GenerationError("source-native corpus undated Position count is impossible")
        if value["status"] == "PASS" and (
            value["source_systems"] != ["littlesis-bulk", "quorum-legacy-derived"]
            or value["staff_net_required_state"] != "off"
            or value["source_enabled"] is not False
            or value["analysis_enabled"] is not False
            or value["canonical_entity_merges"] != 0
            or value["cross_source_identity_assertions"] != 0
            or value["identity_resolution_advanced"] is not False
            or value["politico_adjudication_queue_touched"] is not False
            or value["lda_gate_status"] != "CLOSED"
            or value["politico_gate_status"] != "CLOSED"
            or value["loadable_standalone"] is not True
            or value["share_alike_publication_review_deferred"] is not True
            or value["repository_payload"] != "aggregate-receipt-only"
            or value["emitted_endpoint_missing"] != 0
            or value["temporal_weight_nonzero"] != 0
            or value["failures"]
        ):
            raise GenerationError("source-native corpus PASS violates quarantine controls")
    elif schema_id == "staffer-network-source-native-corpus-determinism-receipt-v1":
        expected_roles = {
            "littlesis-entities", "littlesis-relationship-assertions",
            "quorum-legacy-source-native-seed", "source-native-corpus-receipt",
        }
        rows = value["verification_files"]
        if {row["role"] for row in rows} != expected_roles:
            raise GenerationError("corpus determinism file inventory is incomplete")
        if value["status"] == "PASS" and (
            value["corrected_twin_count"] < 2
            or value["all_files_byte_identical"] is not True
            or any(row["identical"] is not True for row in rows)
            or value["nondeterminism_detected_and_remediated"] is not True
            or len(value["prior_nondeterministic_receipt_sha256"]) < 2
            or value["failures"]
        ):
            raise GenerationError("corpus determinism PASS is not supported by twin evidence")
    elif schema_id == "staffer-network-source-native-corpus-validation-receipt-v1":
        expected_roles = {
            "littlesis-entities", "littlesis-relationship-assertions",
            "quorum-legacy-source-native-seed",
        }
        rows = value["files"]
        if {row["role"] for row in rows} != expected_roles:
            raise GenerationError("corpus validation file inventory is incomplete")
        if sum(row["records_validated"] for row in rows) != value["total_records_validated"]:
            raise GenerationError("corpus validation record counts do not conserve")
        if value["status"] == "PASS" and (
            value["all_schema_validation_pass"] is not True
            or value["all_semantic_validation_pass"] is not True
            or any(row["schema_validation_pass"] is not True for row in rows)
            or any(row["semantic_validation_pass"] is not True for row in rows)
            or value["failures"]
        ):
            raise GenerationError("corpus validation PASS lacks full row evidence")


def _records(path: Path, schema: Mapping[str, Any], schema_id: str) -> list[Any]:
    values: list[Any] = []
    if path.name.endswith(".ndjson.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    raise GenerationError(f"blank NDJSON line {path.name}:{line_number}")
                value = json.loads(line)
                assert_public_safe(value)
                validate_instance(value, schema)
                if isinstance(value, Mapping):
                    _validate_record_semantics(value, schema_id)
                values.append(value)
            return values
    value = json.loads(path.read_text(encoding="utf-8"))
    assert_public_safe(value)
    validate_instance(value, schema)
    if isinstance(value, Mapping):
        _validate_record_semantics(value, schema_id)
    return list(value) if isinstance(value, list) else [value]


def publish_generation(
    root: str | Path,
    *,
    generation_id: str,
    build_as_of: str,
    derivation_version: str,
    source_manifest_registry: Mapping[
        str, tuple[Mapping[str, Any], str | Path]
    ],
    artifacts: Mapping[str, tuple[bytes, str, int]],
    schema_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Publish all artifacts, then atomically make the generation selectable.

    ``artifacts`` maps basename to ``(body, schema_id, record_count)``.  A
    generation ID is immutable: attempting to republish it fails.
    """

    if not generation_id or any(char in generation_id for char in "/\\"):
        raise GenerationError("generation_id must be one safe path segment")
    if not artifacts:
        raise GenerationError("generation must contain at least one artifact")
    if "coverage.json" not in artifacts or artifacts["coverage.json"][1] != "staffer-network-coverage-v1":
        raise GenerationError("generation must contain coverage.json under the coverage schema")
    restricted_schema_ids = sorted({
        schema_id
        for _, schema_id, _ in artifacts.values()
        if schema_id in RESTRICTED_SCHEMA_IDS
    })
    if restricted_schema_ids:
        raise GenerationError(
            "restricted-local-only schema cannot be published: "
            + ", ".join(restricted_schema_ids)
        )
    if not source_manifest_registry:
        raise GenerationError("source_manifest_registry must be nonempty")
    source_manifests: list[dict[str, str]] = []
    for locator, (document, base_directory) in sorted(source_manifest_registry.items()):
        if not isinstance(locator, str) or not locator.strip():
            raise GenerationError("source manifest locator must be nonempty")
        validate_manifest_files(document, base_directory)
        if (
            document.get("enabled") is not True
            or document.get("authorization_status") != "AUTHORIZED"
            or document.get("acceptance_bar", {}).get("pass") is not True
        ):
            raise GenerationError("publication source manifest is not enabled and authorized")
        if document.get("redistribution_class") not in {
            "public", "public-safe-derived-only",
        }:
            raise GenerationError(
                "publication source manifest redistribution class is not public"
            )
        digest = document.get("manifest_sha256")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise GenerationError("source manifest is not sealed")
        source_manifests.append(
            {
                "locator": locator,
                "source_system_id": str(document["source_system_id"]),
                "manifest_sha256": digest,
            }
        )

    root_path = Path(root)
    schemas = Path(schema_directory or Path(__file__).with_name("schemas"))
    generations = root_path / "generations"
    final_directory = generations / generation_id
    if final_directory.exists():
        raise GenerationError(f"immutable generation already exists: {generation_id}")
    generations.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{generation_id}.tmp-{os.getpid()}-", dir=generations))
    try:
        file_entries: list[dict[str, Any]] = []
        for name, (body, schema_id, records) in sorted(artifacts.items()):
            if Path(name).name != name:
                raise GenerationError(f"artifact name must be a basename: {name}")
            schema = schema_path(schemas, schema_id)
            target = staging / name
            atomic_write_bytes(target, body)
            file_entries.append(
                {
                    "path": name,
                    "schema_id": schema_id,
                    "schema_sha256": sha256_file(schema),
                    "sha256": sha256_bytes(body),
                    "bytes": len(body),
                    "records": records,
                    "privacy_class": PRIVACY_CLASS,
                }
            )
        manifest = {
            "schema_id": MANIFEST_SCHEMA_ID,
            "generation_id": generation_id,
            "build_as_of": build_as_of,
            "derivation_version": derivation_version,
            "source_manifests": source_manifests,
            "files": file_entries,
        }
        manifest_body = canonical_json_bytes(manifest)
        atomic_write_bytes(staging / "staffer_network_manifest.json", manifest_body)

        # Validate the complete unreachable generation before selection.
        _validate_generation_directory(staging, manifest, schemas)
        os.replace(staging, final_directory)
        pointer = {
            "schema_id": POINTER_SCHEMA_ID,
            "generation_id": generation_id,
            "manifest_sha256": sha256_bytes(manifest_body),
        }
        atomic_write_bytes(root_path / POINTER_NAME, canonical_json_bytes(pointer))
        return pointer
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_generation_directory(directory: Path, manifest: Mapping[str, Any], schemas: Path) -> None:
    manifest_schema = json.loads(schema_path(schemas, MANIFEST_SCHEMA_ID).read_text(encoding="utf-8"))
    try:
        validate_instance(manifest, manifest_schema)
    except SchemaValidationError as exc:
        raise GenerationError(f"invalid generation manifest: {exc}") from exc
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise GenerationError("manifest files must be nonempty")
    restricted_schema_ids = sorted({
        str(entry.get("schema_id"))
        for entry in entries
        if isinstance(entry, Mapping)
        and entry.get("schema_id") in RESTRICTED_SCHEMA_IDS
    })
    if restricted_schema_ids:
        raise GenerationError(
            "restricted-local-only schema cannot be loaded: "
            + ", ".join(restricted_schema_ids)
        )
    advertised_names = {entry.get("path") for entry in entries if isinstance(entry, Mapping)}
    expected_names = advertised_names | {"staffer_network_manifest.json"}
    actual_names = {path.name for path in directory.iterdir() if path.is_file()}
    if any(path.is_dir() for path in directory.iterdir()) or actual_names != expected_names:
        raise GenerationError("generation directory inventory is not closed")
    edge_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise GenerationError("manifest file entry must be an object")
        name = entry.get("path")
        if not isinstance(name, str) or Path(name).name != name:
            raise GenerationError(f"unsafe artifact path: {name!r}")
        target = directory / name
        if not target.is_file():
            raise GenerationError(f"missing advertised artifact: {name}")
        if target.stat().st_size != entry.get("bytes"):
            raise GenerationError(f"byte count mismatch: {name}")
        if sha256_file(target) != entry.get("sha256"):
            raise GenerationError(f"content hash mismatch: {name}")
        schema = schema_path(schemas, str(entry.get("schema_id")))
        if sha256_file(schema) != entry.get("schema_sha256"):
            raise GenerationError(f"schema hash mismatch: {name}")
        if entry.get("privacy_class") != PRIVACY_CLASS:
            raise GenerationError(f"invalid privacy class: {name}")
        schema_document = json.loads(schema.read_text(encoding="utf-8"))
        try:
            records = _records(target, schema_document, str(entry.get("schema_id")))
        except (SchemaValidationError, json.JSONDecodeError) as exc:
            raise GenerationError(f"schema validation failed: {name}: {exc}") from exc
        if len(records) != entry.get("records"):
            raise GenerationError(f"record count mismatch: {name}")
        for record in records:
            if isinstance(record, Mapping) and "edge_id" in record:
                edge_id = str(record["edge_id"])
                if edge_id in edge_ids:
                    raise GenerationError(f"duplicate generation edge_id: {edge_id}")
                edge_ids.add(edge_id)


def load_current_generation(
    root: str | Path, *, schema_directory: str | Path | None = None
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Resolve and return verified artifact bytes, never a mutable path handle."""

    root_path = Path(root)
    schemas = Path(schema_directory or Path(__file__).with_name("schemas"))
    pointer_path = root_path / POINTER_NAME
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer_schema = json.loads(schema_path(schemas, POINTER_SCHEMA_ID).read_text(encoding="utf-8"))
    try:
        validate_instance(pointer, pointer_schema)
    except SchemaValidationError as exc:
        raise GenerationError(f"invalid current pointer: {exc}") from exc
    generation_id = str(pointer.get("generation_id"))
    if not generation_id or any(char in generation_id for char in "/\\"):
        raise GenerationError("current pointer generation_id must be one safe path segment")
    generation = root_path / "generations" / generation_id
    manifest_path = generation / "staffer_network_manifest.json"
    if sha256_file(manifest_path) != pointer.get("manifest_sha256"):
        raise GenerationError("selected manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("generation_id") != pointer.get("generation_id"):
        raise GenerationError("pointer and manifest generation IDs disagree")
    _validate_generation_directory(generation, manifest, schemas)
    artifacts: dict[str, bytes] = {}
    for entry in manifest["files"]:
        target = generation / entry["path"]
        body = target.read_bytes()
        if sha256_bytes(body) != entry["sha256"]:
            raise GenerationError(f"artifact changed during verified read: {entry['path']}")
        artifacts[entry["path"]] = body
    return manifest, artifacts
