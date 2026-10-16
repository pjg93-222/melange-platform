# -*- coding: utf-8 -*-
"""Restricted employment-history lookup and temporal-analysis sidecar.

The LDA covered-position field is a discovery seed, not a dated employment
episode.  This module keeps that boundary explicit: the queue performs no
identity merge, while temporal functions accept only separately adjudicated
employment episodes and use half-open uncertain intervals.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
import argparse
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit

try:
    from .intervals import UncertainInterval
    from .lda_source_native_corpus import _government_terms
    from .publication import _validate_record_semantics
    from .schema_validation import validate_instance
    from .source_manifest import canonical_json_bytes, sha256_file
except ImportError:  # pragma: no cover - direct script invocation
    from staffer_network.intervals import UncertainInterval
    from staffer_network.lda_source_native_corpus import _government_terms
    from staffer_network.publication import _validate_record_semantics
    from staffer_network.schema_validation import validate_instance
    from staffer_network.source_manifest import canonical_json_bytes, sha256_file


LOOKUP_SCHEMA_ID = "staffer-network-employment-lookup-candidate-v1"
LOOKUP_RECEIPT_SCHEMA_ID = "staffer-network-employment-lookup-receipt-v1"
LEGISTORM_CAPTURE_SCHEMA_ID = "staffer-network-legistorm-employment-capture-v1"
LEGISTORM_OBSERVATION_SCHEMA_ID = "staffer-network-legistorm-employment-observation-v1"
EMPLOYMENT_DECISION_SCHEMA_ID = "staffer-network-employment-adjudication-decision-v1"
LEGISTORM_CHECKPOINT_RECEIPT_SCHEMA_ID = (
    "staffer-network-legistorm-employment-checkpoint-receipt-v1"
)
EPISODE_SCHEMA_ID = "staffer-network-employment-episode-v1"
TEMPORAL_PATH_SCHEMA_ID = "staffer-network-temporal-path-v1"
TEMPORAL_EDGE_SCHEMA_ID = "staffer-network-temporal-edge-v1"
TEMPORAL_RECEIPT_SCHEMA_ID = "staffer-network-temporal-build-receipt-v1"
HISTORY_COMPLETENESS_AUDIT_SCHEMA_ID = (
    "staffer-network-employment-completeness-audit-v2"
)
LOOKUP_METHOD_VERSION = "lda-covered-position-employment-lookup-v1"
LEGISTORM_CHECKPOINT_METHOD_VERSION = "legistorm-local-employment-checkpoint-v1"
TEMPORAL_METHOD_VERSION = "employment-temporal-sidecar-v1"
SCHEMA_DIRECTORY = Path(__file__).resolve().parent / "schemas"
CANONICAL_EMPLOYMENT_EPISODE_SCHEMA = json.loads(
    (SCHEMA_DIRECTORY / "employment_episode.schema.json").read_text(encoding="utf-8")
)
YEAR_TOKEN = re.compile(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)")
HISTORY_AUDIT_MIN_YEAR = 1900
HISTORY_AUDIT_MAX_YEAR = datetime.now(timezone.utc).year + 1
MONTH_TOKEN = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
HILL_KINDS = frozenset({"member_office", "committee", "leadership", "admin"})
# Owner ruling 2026-09-07: chamber-wide ADMINISTRATIVE payroll headers leave the
# co-tenure layer. They remain full members of HILL_KINDS everywhere else -- they
# stay in the graph, stay reachable, and still anchor employment episodes -- but no
# person-person co-tenure pair is enumerated inside one.
#
# Why: the 54 `admin` containers (Chief Administrative Officer of the House 2,551
# people; Office of Congressional Accessibility Services 3,373; Sergeant at Arms
# Senate 2,059; Clerk of the House 1,118; SGT@ARMS Capitol Division 1,576; and 49
# others) carried 70.8% of every co-tenure pair in the v5 generation. Concurrency
# was enforced correctly; the UNIT was wrong. Appearing under the same chamber-wide
# payroll header in the same period says two people were paid by the same
# institution, not that they shared a workplace, so a clique over that header
# manufactures the bulk of the published figure out of an accounting artifact.
CO_TENURE_EXCLUDED_CONTAINER_KINDS = frozenset({"admin"})
CO_TENURE_KINDS = (
    HILL_KINDS | frozenset({"registrant", "other_org"})
) - CO_TENURE_EXCLUDED_CONTAINER_KINDS
INDUSTRY_KINDS = frozenset({"registrant", "client_activity"})
ADJUDICATION_STATUSES = frozenset(
    {"ACCEPTED", "REJECTED", "AMBIGUOUS", "REVIEW_REQUIRED"}
)
COMPLETENESS_ADJUDICATION_STATUSES = frozenset({"PASS", "FAIL"})
LEGISTORM_PROFILE_URL = re.compile(
    r"^https://www\.legistorm\.com/person/bio/(?P<profile_id>[1-9][0-9]*)/[^?#]+\.html$"
)


class EmploymentHistoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanonicalEmploymentProvenance:
    """Hash-bound registry of canonical episode payloads from a verified generation."""

    generation_receipt_path: str
    generation_receipt_sha256: str
    _episode_bindings: tuple[tuple[str, str, str], ...] = dataclass_field(
        init=False, repr=False
    )
    _binding_index: dict[str, tuple[str, str]] = dataclass_field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        bindings = _verified_canonical_episode_bindings(
            self.generation_receipt_path,
            expected_sha256=self.generation_receipt_sha256,
        )
        object.__setattr__(
            self,
            "_episode_bindings",
            tuple(
                sorted(
                    (episode_id, payload_sha256, lda_source_person_id)
                    for episode_id, (payload_sha256, lda_source_person_id)
                    in bindings.items()
                )
            ),
        )
        object.__setattr__(
            self,
            "_binding_index",
            {
                episode_id: (payload_sha256, source_person_id)
                for episode_id, payload_sha256, source_person_id
                in self._episode_bindings
            },
        )

    def binds(
        self, episode: Mapping[str, Any], lda_source_person_id: str
    ) -> bool:
        episode_id = episode.get("episode_id")
        return (
            isinstance(episode_id, str)
            and self._binding_index.get(episode_id)
            == (
                hashlib.sha256(canonical_json_bytes(episode)).hexdigest(),
                lda_source_person_id.strip().casefold(),
            )
        )

    def binds_complete_set(
        self,
        episodes: Sequence[Mapping[str, Any]],
        lda_source_person_id: str,
    ) -> bool:
        normalized_person_id = lda_source_person_id.strip().casefold()
        expected_ids = {
            episode_id
            for episode_id, _payload_sha256, source_person_id
            in self._episode_bindings
            if source_person_id == normalized_person_id
        }
        observed_ids = [episode.get("episode_id") for episode in episodes]
        if any(not isinstance(value, str) for value in observed_ids):
            return False
        return (
            len(observed_ids) == len(set(observed_ids))
            and set(observed_ids) == expected_ids
            and all(
                isinstance(episode, Mapping)
                and self.binds(episode, normalized_person_id)
                for episode in episodes
            )
        )


def employment_episode_digest(episodes: Sequence[Any]) -> str:
    """Return an order-independent digest binding an attestation to episodes."""

    episode_hashes = sorted(
        hashlib.sha256(canonical_json_bytes(episode)).hexdigest()
        for episode in episodes
    )
    return hashlib.sha256(canonical_json_bytes(episode_hashes)).hexdigest()


def _has_date_evidence(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    try:
        timestamp = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        timestamp = None
    if timestamp is not None and timestamp.tzinfo is not None:
        return HISTORY_AUDIT_MIN_YEAR <= timestamp.year <= HISTORY_AUDIT_MAX_YEAR
    iso_like = re.fullmatch(r"(18|19|20)\d{2}(?:-(\d{1,2}))?(?:-(\d{1,2}))?", normalized)
    if iso_like is not None:
        year = int(normalized[:4])
        if not HISTORY_AUDIT_MIN_YEAR <= year <= HISTORY_AUDIT_MAX_YEAR:
            return False
        month_text, day_text = iso_like.group(2), iso_like.group(3)
        if month_text is None:
            return True
        month = int(month_text)
        if not 1 <= month <= 12:
            return False
        if day_text is None:
            return True
        try:
            date(int(normalized[:4]), month, int(day_text))
        except ValueError:
            return False
        return True
    month_year = re.fullmatch(
        r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+"
        r"(?:18|19|20)\d{2}",
        normalized,
        re.IGNORECASE,
    )
    if month_year is not None:
        year = int(YEAR_TOKEN.search(normalized).group(0))
        return HISTORY_AUDIT_MIN_YEAR <= year <= HISTORY_AUDIT_MAX_YEAR
    numeric_month_year = re.fullmatch(
        r"(\d{1,2})/(?:18|19|20)\d{2}", normalized
    )
    if numeric_month_year is not None:
        year = int(YEAR_TOKEN.search(normalized).group(0))
        return (
            1 <= int(numeric_month_year.group(1)) <= 12
            and HISTORY_AUDIT_MIN_YEAR <= year <= HISTORY_AUDIT_MAX_YEAR
        )
    quarter = re.fullmatch(
        r"(?:Q[1-4]\s+(?:18|19|20)\d{2}|(?:18|19|20)\d{2}\s+Q[1-4])",
        normalized,
        re.IGNORECASE,
    )
    if quarter is None:
        return False
    year = int(YEAR_TOKEN.search(normalized).group(0))
    return HISTORY_AUDIT_MIN_YEAR <= year <= HISTORY_AUDIT_MAX_YEAR


def _date_evidence_bound(value: Any, boundary: str) -> date | None:
    """Return a comparable conservative date for one validated date atom."""

    if boundary not in {"start", "end"} or not _has_date_evidence(value):
        return None
    normalized = str(value).strip()
    try:
        timestamp = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        timestamp = None
    if timestamp is not None and timestamp.tzinfo is not None:
        return timestamp.date()
    iso_like = re.fullmatch(
        r"(18|19|20)\d{2}(?:-(\d{1,2}))?(?:-(\d{1,2}))?", normalized
    )
    if iso_like is not None:
        year = int(normalized[:4])
        month_text, day_text = iso_like.group(2), iso_like.group(3)
        if month_text is None:
            return date(year, 1, 1) if boundary == "start" else date(year, 12, 31)
        month = int(month_text)
        if day_text is not None:
            return date(year, month, int(day_text))
        if boundary == "start":
            return date(year, month, 1)
        next_month = (
            date(year + 1, 1, 1)
            if month == 12
            else date(year, month + 1, 1)
        )
        return next_month - timedelta(days=1)
    year = int(YEAR_TOKEN.search(normalized).group(0))
    quarter = re.search(r"Q([1-4])", normalized, re.IGNORECASE)
    if quarter is not None:
        month = (int(quarter.group(1)) - 1) * 3 + 1
    else:
        month_name = MONTH_TOKEN.search(normalized)
        if month_name is not None:
            month = [
                "jan", "feb", "mar", "apr", "may", "jun",
                "jul", "aug", "sep", "oct", "nov", "dec",
            ].index(month_name.group(0)[:3].lower()) + 1
        else:
            month = int(normalized.split("/", 1)[0])
    if boundary == "start":
        return date(year, month, 1)
    months = 3 if quarter is not None else 1
    end_month_index = month - 1 + months
    end_year = year + end_month_index // 12
    next_month = end_month_index % 12 + 1
    return date(end_year, next_month, 1) - timedelta(days=1)


def _raw_date_bounds(
    value: Any,
) -> tuple[bool, bool, bool, str | None, str | None]:
    """Parse only strict raw date atoms/ranges; never rescue arbitrary prose."""

    if not isinstance(value, str) or not value.strip():
        return False, False, False, None, None
    normalized = re.sub(r"[\u2012\u2013\u2014\u2212]", "-", value.strip())
    current_match = re.fullmatch(
        r"(.+?)(?:\s+(?:to|through|until)\s+|\s*-\s*)"
        r"(present|current|currently)",
        normalized,
        re.IGNORECASE,
    )
    if current_match is not None:
        start_atom = current_match.group(1)
        start_present = _has_date_evidence(start_atom)
        return start_present, False, start_present, (
            start_atom if start_present else None
        ), None
    open_match = re.fullmatch(r"(.+?)\s*-\s*", normalized)
    if open_match is not None and _has_date_evidence(open_match.group(1)):
        return True, False, True, open_match.group(1), None
    for separator in (r"\s+to\s+", r"\s+through\s+", r"\s+until\s+"):
        pieces = re.split(separator, normalized, maxsplit=1, flags=re.IGNORECASE)
        if len(pieces) == 2 and all(_has_date_evidence(piece) for piece in pieces):
            return True, True, False, pieces[0], pieces[1]
    for index, character in enumerate(normalized):
        if character != "-":
            continue
        left, right = normalized[:index].strip(), normalized[index + 1 :].strip()
        if _has_date_evidence(left) and _has_date_evidence(right):
            return True, True, False, left, right
    slash_range = re.fullmatch(
        r"((?:18|19|20)\d{2})/((?:18|19|20)\d{2})", normalized
    )
    if slash_range is not None and all(
        _has_date_evidence(value) for value in slash_range.groups()
    ):
        return True, True, False, slash_range.group(1), slash_range.group(2)
    present = _has_date_evidence(normalized)
    return present, False, False, normalized if present else None, None


def _is_http_source(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value.strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc.strip())


def _is_attributed_attestation(attestation: Mapping[str, Any]) -> bool:
    return all(
        isinstance(attestation.get(key), str) and bool(attestation[key].strip())
        for key in ("reviewer_kind", "reviewer_id", "rationale")
    )


def _has_valid_attestation_time(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def audit_history_completeness(
    record: Mapping[str, Any],
    *,
    canonical_provenance: CanonicalEmploymentProvenance | None = None,
) -> dict[str, Any]:
    """Fail closed unless a person's employment chronology is exhaustive.

    Episode presence is not evidence of completeness. A record can pass only
    when every emitted employment episode is dated and sourced and an
    adjudicator has explicitly attested that the employment history has no
    remaining gap. Education is retained as nonblocking enrichment and its
    deficiencies are emitted separately as diagnostics.
    """

    failures: list[str] = []
    education_diagnostics: list[str] = []
    employment = record.get("employment_episodes", record.get("employment_history"))
    education = record.get("education", record.get("education_history"))
    if not isinstance(employment, list) or not employment:
        failures.append("EMPLOYMENT_HISTORY_EMPTY")
        employment = []
    if not isinstance(education, list) or not education:
        education_diagnostics.append("EDUCATION_HISTORY_EMPTY")
        education = []

    top_level_person_id = record.get("lda_source_person_id")
    identity = record.get("person_identity")
    nested_person_id = (
        identity.get("lda_source_person_id") if isinstance(identity, Mapping) else None
    )
    if (
        isinstance(top_level_person_id, str)
        and top_level_person_id.strip()
        and isinstance(nested_person_id, str)
        and nested_person_id.strip()
        and top_level_person_id.strip().casefold()
        != nested_person_id.strip().casefold()
    ):
        failures.append("LDA_SOURCE_PERSON_ID_CONFLICT")
    person_id = (
        top_level_person_id
        if isinstance(top_level_person_id, str)
        else nested_person_id
    )
    if (
        not isinstance(person_id, str)
        or not person_id.strip()
        or person_id.strip().upper() == "UNRESOLVED"
    ):
        failures.append("LDA_SOURCE_PERSON_ID_MISSING")
        person_id = "UNRESOLVED"
    else:
        person_id = person_id.strip().casefold()

    for index, episode in enumerate(employment, start=1):
        prefix = f"EMPLOYMENT_EPISODE_{index}"
        if not isinstance(episode, Mapping):
            failures.append(prefix + "_INVALID")
            continue
        if episode.get("schema_id") != "staffer-network-employment-episode-v1":
            failures.append(prefix + "_CANONICAL_SCHEMA_REQUIRED")
        else:
            try:
                validate_instance(episode, CANONICAL_EMPLOYMENT_EPISODE_SCHEMA)
            except Exception:
                failures.append(prefix + "_CANONICAL_SCHEMA_INVALID")
            else:
                try:
                    _validate_record_semantics(episode, episode["schema_id"])
                except Exception:
                    failures.append(prefix + "_CANONICAL_SEMANTICS_INVALID")
            if canonical_provenance is None:
                failures.append(prefix + "_CANONICAL_PROVENANCE_UNVERIFIED")
            elif not canonical_provenance.binds(episode, person_id):
                failures.append(prefix + "_CANONICAL_PROVENANCE_MISMATCH")
        if episode.get("episode_status") != "ACTIVE":
            failures.append(prefix + "_ACTIVE_STATUS_REQUIRED")
        if episode.get("identity_status") != "ACCEPTED":
            failures.append(prefix + "_IDENTITY_ACCEPTED_REQUIRED")
        if episode.get("container_status") != "ACCEPTED":
            failures.append(prefix + "_CONTAINER_ACCEPTED_REQUIRED")
        if "boundary_status" in episode and episode.get("boundary_status") != "ACCEPTED":
            failures.append(prefix + "_BOUNDARY_ACCEPTED_REQUIRED")
        if episode.get("promotion_blockers"):
            failures.append(prefix + "_PROMOTION_BLOCKERS_REMAIN")
        if "canonical_eligible" in episode and episode.get("canonical_eligible") is not True:
            failures.append(prefix + "_CANONICAL_ELIGIBILITY_REQUIRED")
        if "temporal_eligible" in episode and episode.get("temporal_eligible") is not True:
            failures.append(prefix + "_TEMPORAL_ELIGIBILITY_REQUIRED")
        employer = episode.get(
            "employer",
            episode.get(
                "raw_employer",
                episode.get("normalized_container_id", episode.get("organization_id")),
            ),
        )
        title = episode.get("title", episode.get("raw_title"))
        if not isinstance(employer, str) or not employer.strip():
            failures.append(prefix + "_EMPLOYER_MISSING")
        if not isinstance(title, str) or not title.strip():
            failures.append(prefix + "_TITLE_MISSING")
        start_present = any(
            _has_date_evidence(episode.get(key))
            for key in ("start_date", "start_earliest", "raw_start")
        )
        asserted_current = episode.get("asserted_current") is True
        current_as_of_present = any(
            _has_date_evidence(episode.get(key))
            for key in ("current_as_of", "source_as_of", "retrieved_at", "observed_at")
        )
        end_present = any(
            _has_date_evidence(episode.get(key))
            for key in ("end_date", "end_earliest", "raw_end")
        ) or (asserted_current and current_as_of_present)
        raw_dates = episode.get("raw_dates")
        raw_current_marker = False
        (
            raw_start_present,
            raw_end_present,
            raw_current_marker,
            raw_start_atom,
            raw_end_atom,
        ) = _raw_date_bounds(raw_dates)
        start_present = start_present or raw_start_present
        end_present = end_present or raw_end_present or (
            raw_current_marker and current_as_of_present
        )
        if not start_present:
            failures.append(prefix + "_START_MISSING")
        if not end_present:
            failures.append(prefix + "_END_MISSING")
        if (asserted_current or raw_current_marker) and not current_as_of_present:
            failures.append(prefix + "_CURRENT_AS_OF_MISSING")
        start_bounds = [
            bound
            for value in (
                episode.get("start_date"),
                episode.get("start_earliest"),
                episode.get("raw_start"),
                raw_start_atom,
            )
            if (bound := _date_evidence_bound(value, "start")) is not None
        ]
        end_values = [
            episode.get("end_date"),
            episode.get("end_earliest"),
            episode.get("raw_end"),
            raw_end_atom,
        ]
        if asserted_current or raw_current_marker:
            end_values.extend(
                episode.get(key)
                for key in (
                    "current_as_of",
                    "source_as_of",
                    "retrieved_at",
                    "observed_at",
                )
            )
        end_bounds = [
            bound
            for value in end_values
            if (bound := _date_evidence_bound(value, "end")) is not None
        ]
        if start_bounds and end_bounds and max(start_bounds) > min(end_bounds):
            failures.append(prefix + "_DATE_ORDER_INVALID")
        sources = episode.get("source_urls")
        source = episode.get("source_url")
        support_ids = episode.get("support_observation_ids")
        support_digest = episode.get("support_digest")
        bound_support = (
            isinstance(support_ids, list)
            and bool(support_ids)
            and all(isinstance(value, str) and value for value in support_ids)
            and isinstance(support_digest, str)
            and support_digest == _support_digest(support_ids)
        )
        if not (
            (isinstance(sources, list) and any(_is_http_source(v) for v in sources))
            or _is_http_source(source)
            or bound_support
        ):
            failures.append(prefix + "_SOURCE_MISSING")

    if canonical_provenance is not None and not canonical_provenance.binds_complete_set(
        [episode for episode in employment if isinstance(episode, Mapping)], person_id
    ):
        failures.append("CANONICAL_EPISODE_SET_MISMATCH")

    for index, episode in enumerate(education, start=1):
        prefix = f"EDUCATION_EPISODE_{index}"
        if not isinstance(episode, Mapping):
            education_diagnostics.append(prefix + "_INVALID")
            continue
        institution = episode.get("institution")
        if not isinstance(institution, str) or not institution.strip():
            education_diagnostics.append(prefix + "_INSTITUTION_MISSING")
        attendance_present = any(
            episode.get(key) not in (None, "", [])
            for key in (
                "years", "class_year", "class_year_lead", "start_date",
                "start_earliest", "end_date", "end_earliest",
            )
        )
        if not attendance_present:
            education_diagnostics.append(prefix + "_ATTENDANCE_DATES_MISSING")
        sources = episode.get("source_urls")
        source = episode.get("source_url")
        if not (
            (isinstance(sources, list) and any(_is_http_source(v) for v in sources))
            or _is_http_source(source)
        ):
            education_diagnostics.append(prefix + "_SOURCE_MISSING")

    attestation = record.get("history_completeness_attestation")
    if not isinstance(attestation, Mapping):
        failures.append("EXHAUSTIVENESS_ATTESTATION_MISSING")
        education_diagnostics.append("EDUCATION_EXHAUSTIVENESS_NOT_ATTESTED")
    else:
        if not _is_attributed_attestation(attestation):
            failures.append("EXHAUSTIVENESS_ATTESTATION_UNATTRIBUTED")
        if not _has_valid_attestation_time(attestation.get("attested_at")):
            failures.append("EXHAUSTIVENESS_ATTESTATION_TIME_INVALID")
        if attestation.get("employment_exhaustive") is not True:
            failures.append("EMPLOYMENT_EXHAUSTIVENESS_NOT_ATTESTED")
        if attestation.get("education_exhaustive") is not True:
            education_diagnostics.append("EDUCATION_EXHAUSTIVENESS_NOT_ATTESTED")
        gap_fields = [
            value
            for value in (
                attestation.get("unresolved_employment_gaps"),
                attestation.get("unresolved_gaps"),
            )
            if value is not None
        ]
        if not gap_fields or any(not isinstance(value, list) for value in gap_fields):
            failures.append("UNRESOLVED_EMPLOYMENT_GAPS_INVALID")
        elif any(gap_fields):
            failures.append("UNRESOLVED_EMPLOYMENT_GAPS_REMAIN")
        attested_digest = attestation.get("attested_episode_digest")
        observed_digest = employment_episode_digest(employment)
        if not isinstance(attested_digest, str) or attested_digest != observed_digest:
            failures.append("EXHAUSTIVENESS_ATTESTATION_UNBOUND")
        education_gaps = attestation.get("unresolved_education_gaps")
        if education_gaps is not None:
            if not isinstance(education_gaps, list):
                education_diagnostics.append("UNRESOLVED_EDUCATION_GAPS_INVALID")
            elif education_gaps:
                education_diagnostics.append("UNRESOLVED_EDUCATION_GAPS_REMAIN")
        adjudication_status = attestation.get("adjudication_status")
        if adjudication_status not in COMPLETENESS_ADJUDICATION_STATUSES:
            failures.append("EXHAUSTIVENESS_ADJUDICATION_STATUS_INVALID")
        if adjudication_status != "PASS":
            failures.append("EXHAUSTIVENESS_ADJUDICATION_NOT_PASS")

    employment_status = "COMPLETE" if not failures else "INCOMPLETE"
    education_status = (
        "MISSING"
        if not education
        else ("PARTIAL" if education_diagnostics else "COMPLETE")
    )
    return {
        "schema_id": HISTORY_COMPLETENESS_AUDIT_SCHEMA_ID,
        "lda_source_person_id": person_id,
        "display_name": record.get("display_name"),
        "status": employment_status,
        "employment_status": employment_status,
        "education_status": education_status,
        "binding_completeness_dimension": "EMPLOYMENT",
        "employment_episode_count": len(employment),
        "education_episode_count": len(education),
        "failure_codes": sorted(set(failures)),
        "education_diagnostic_codes": sorted(set(education_diagnostics)),
    }


def audit_history_corpus(
    record_paths: Sequence[str | Path],
    output_root: str | Path,
    *,
    canonical_provenance: CanonicalEmploymentProvenance | None = None,
) -> dict[str, Any]:
    """Audit enrichment NDJSON files and emit deterministic per-person results."""

    output = Path(output_root)
    if output.exists() and any(output.iterdir()):
        raise EmploymentHistoryError(f"output root is nonempty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    paths = [Path(value) for value in record_paths]
    if not paths:
        raise EmploymentHistoryError("at least one history record path is required")
    result_schema = _read_json(
        SCHEMA_DIRECTORY / "history_completeness_audit.schema.json"
    )
    results: list[dict[str, Any]] = []
    for path in paths:
        for row in _rows(path):
            result = audit_history_completeness(
                row, canonical_provenance=canonical_provenance
            )
            result["source_path"] = str(path)
            validate_instance(result, result_schema)
            results.append(result)
    result_path = output / "history_completeness_audit.ndjson.gz"
    _write_sorted_gzip(
        result_path,
        results,
        key=lambda row: (
            str(row.get("lda_source_person_id")), str(row.get("source_path"))
        ),
    )
    counts = Counter(row["status"] for row in results)
    identified_statuses: dict[str, list[str]] = defaultdict(list)
    unidentified_rows = 0
    for row in results:
        person_id = row["lda_source_person_id"]
        if person_id == "UNRESOLVED":
            unidentified_rows += 1
        else:
            identified_statuses[person_id].append(row["status"])
    complete_people = sum(
        len(statuses) == 1 and statuses[0] == "COMPLETE"
        for statuses in identified_statuses.values()
    )
    incomplete_people = sum(
        len(statuses) != 1 or any(status == "INCOMPLETE" for status in statuses)
        for statuses in identified_statuses.values()
    )
    duplicate_identified_person_rows = (
        sum(len(statuses) for statuses in identified_statuses.values())
        - len(identified_statuses)
    )
    identified_row_count = sum(
        len(statuses) for statuses in identified_statuses.values()
    )
    if counts["COMPLETE"] + counts["INCOMPLETE"] != len(results):
        raise EmploymentHistoryError("audit row status conservation failed")
    if identified_row_count + unidentified_rows != len(results):
        raise EmploymentHistoryError("audit identity-row conservation failed")
    if (
        len(identified_statuses) + duplicate_identified_person_rows
        != identified_row_count
    ):
        raise EmploymentHistoryError("audit duplicate-row conservation failed")
    if complete_people + incomplete_people != len(identified_statuses):
        raise EmploymentHistoryError("audit person-status conservation failed")
    failure_counts: Counter[str] = Counter(
        code for row in results for code in row["failure_codes"]
    )
    education_diagnostic_counts: Counter[str] = Counter(
        code for row in results for code in row["education_diagnostic_codes"]
    )
    receipt = {
        "schema_id": "staffer-network-employment-completeness-audit-receipt-v2",
        "generated_at": _now(),
        "status": (
            "PASS"
            if (
                results
                and counts["INCOMPLETE"] == 0
                and unidentified_rows == 0
                and duplicate_identified_person_rows == 0
                and complete_people == len(identified_statuses)
            )
            else "INCOMPLETE"
        ),
        "binding_completeness_dimension": "EMPLOYMENT",
        "education_is_nonblocking": True,
        "record_rows": len(results),
        "identified_people": len(identified_statuses),
        "unidentified_rows": unidentified_rows,
        "duplicate_identified_person_rows": duplicate_identified_person_rows,
        "complete_people": complete_people,
        "incomplete_people": incomplete_people,
        "complete_rows": counts["COMPLETE"],
        "incomplete_rows": counts["INCOMPLETE"],
        "failure_counts": dict(sorted(failure_counts.items())),
        "education_diagnostic_counts": dict(
            sorted(education_diagnostic_counts.items())
        ),
        "source_files": [
            {"path": str(path), "sha256": sha256_file(path)} for path in paths
        ],
        "audit_file": {
            "path": result_path.name,
            "sha256": sha256_file(result_path),
            "bytes": result_path.stat().st_size,
        },
    }
    receipt_schema = _read_json(
        SCHEMA_DIRECTORY / "history_completeness_audit_receipt.schema.json"
    )
    validate_instance(receipt, receipt_schema)
    _write_json(output / "history_completeness_audit_receipt.json", receipt)
    return receipt


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EmploymentHistoryError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EmploymentHistoryError(f"{path} is not a JSON object")
    return value


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise EmploymentHistoryError(
                        f"{path}:{line_number} is not an object"
                    )
                yield value
    except (OSError, json.JSONDecodeError) as exc:
        raise EmploymentHistoryError(f"cannot stream {path}: {exc}") from exc


def _write_sorted_gzip(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    key,
) -> int:
    ordered = sorted(rows, key=key)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            for row in ordered:
                stream.write(canonical_json_bytes(row))
    os.replace(temporary, path)
    return len(ordered)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_bytes(canonical_json_bytes(value))
    os.replace(temporary, path)


def _bound_source_file(path: Path, receipt: Mapping[str, Any], role: str) -> None:
    entries = receipt.get("files")
    if not isinstance(entries, list):
        raise EmploymentHistoryError("LDA receipt lacks a file inventory")
    matches = [
        row for row in entries
        if isinstance(row, dict) and row.get("role") == role
    ]
    if len(matches) != 1:
        raise EmploymentHistoryError(f"ambiguous LDA receipt role: {role}")
    entry = matches[0]
    if entry.get("bytes") != path.stat().st_size or entry.get("sha256") != sha256_file(path):
        raise EmploymentHistoryError(f"LDA receipt binding failed for {path.name}")


def _queries(name: str) -> list[str]:
    safe = " ".join(name.split())
    return [
        f'site:legistorm.com/person/bio/ "{safe}"',
        f'site:legistorm.com/person/ "{safe}" "Congressional Staffer"',
        f'"{safe}" "Employment History" Congress',
    ]


def build_lookup_queue(
    lda_corpus_root: str | Path,
    output_root: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic lookup queue without asserting any web match."""

    lda_root = Path(lda_corpus_root)
    output = Path(output_root)
    if output.exists() and any(output.iterdir()):
        raise EmploymentHistoryError(f"output root is nonempty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    receipt_path = lda_root / "lda_source_native_corpus_receipt.json"
    lobbyist_path = lda_root / "lda_lobbyist_assertions.ndjson.gz"
    source_receipt = _read_json(receipt_path)
    if (
        source_receipt.get("schema_id")
        != "staffer-network-lda-source-native-corpus-receipt-v1"
        or source_receipt.get("status") != "PASS"
        or source_receipt.get("canonical_identity_merges") != 0
        or source_receipt.get("name_matches_are_candidates_only") is not True
    ):
        raise EmploymentHistoryError("LDA source corpus is not a sealed zero-merge PASS")
    _bound_source_file(lobbyist_path, source_receipt, "lobbyist_assertions")

    source_receipt_sha256 = sha256_file(receipt_path)
    source_binding_sha256 = hashlib.sha256(canonical_json_bytes({
        "method_version": LOOKUP_METHOD_VERSION,
        "lda_source_receipt_sha256": source_receipt_sha256,
    })).hexdigest()
    queue: list[dict[str, Any]] = []
    priority_counts: Counter[str] = Counter()
    year_hit_people = 0
    month_hit_people = 0
    for lobbyist in _rows(lobbyist_path):
        source_person_id = lobbyist.get("source_person_id")
        display_name = lobbyist.get("display_name")
        values = lobbyist.get("covered_position_values")
        years = lobbyist.get("filing_years")
        if (
            not isinstance(source_person_id, str)
            or not isinstance(display_name, str)
            or not display_name.strip()
            or not isinstance(values, list)
            or not isinstance(years, list)
        ):
            raise EmploymentHistoryError("invalid LDA lobbyist assertion")
        positions = sorted({str(value).strip() for value in values if str(value).strip()})
        government_hit = any(_government_terms(value) for value in positions)
        year_hits = sorted({match.group(0) for value in positions for match in YEAR_TOKEN.finditer(value)})
        month_hit = any(MONTH_TOKEN.search(value) for value in positions)
        priority = "P0" if government_hit else ("P1" if positions else "P2")
        priority_counts[priority] += 1
        year_hit_people += bool(year_hits)
        month_hit_people += month_hit
        digest = hashlib.sha256(
            f"{source_binding_sha256}\0{source_person_id}".encode("utf-8")
        ).hexdigest()
        row = {
            "schema_id": LOOKUP_SCHEMA_ID,
            "candidate_id": "employment:lookup:" + digest,
            "method_version": LOOKUP_METHOD_VERSION,
            "source_binding_sha256": source_binding_sha256,
            "lda_source_person_id": source_person_id,
            "display_name": display_name.strip(),
            "name_variants": sorted({
                str(value).strip() for value in lobbyist.get("name_variants", [])
                if str(value).strip()
            }),
            "filing_years": sorted({int(value) for value in years}),
            "covered_position_values": positions,
            "government_term_hit": government_hit,
            "covered_position_year_tokens": year_hits,
            "covered_position_month_token": month_hit,
            "priority": priority,
            "search_queries": _queries(display_name),
            "discovery_status": "NOT_STARTED",
            "identity_status": "REVIEW_REQUIRED",
            "canonical_merge": False,
        }
        queue.append(row)

    expected = source_receipt.get("lobbyists")
    if not isinstance(expected, int) or len(queue) != expected:
        raise EmploymentHistoryError(
            f"LDA lobbyist conservation failed: {len(queue)} != {expected}"
        )
    candidate_schema = _read_json(
        SCHEMA_DIRECTORY / "employment_lookup_candidate.schema.json"
    )
    receipt_schema = _read_json(
        SCHEMA_DIRECTORY / "employment_lookup_receipt.schema.json"
    )
    for row in queue:
        validate_instance(row, candidate_schema)
        _validate_record_semantics(row, row["schema_id"])
    queue_path = output / "employment_lookup_queue.ndjson.gz"
    persisted_count = _write_sorted_gzip(
        queue_path, queue, key=lambda row: (row["priority"], row["lda_source_person_id"])
    )
    if persisted_count != len(queue):
        raise EmploymentHistoryError("lookup queue persistence did not reconcile")
    persisted_ids: set[str] = set()
    persisted_priorities: Counter[str] = Counter()
    for row in _rows(queue_path):
        validate_instance(row, candidate_schema)
        _validate_record_semantics(row, row["schema_id"])
        source_person_id = row["lda_source_person_id"]
        if source_person_id in persisted_ids:
            raise EmploymentHistoryError("persisted lookup queue repeats an LDA person")
        persisted_ids.add(source_person_id)
        persisted_priorities[row["priority"]] += 1
    if persisted_ids != {row["lda_source_person_id"] for row in queue}:
        raise EmploymentHistoryError("persisted lookup person universe drifted")
    if persisted_priorities != priority_counts:
        raise EmploymentHistoryError("persisted lookup priorities drifted")

    receipt = {
        "schema_id": LOOKUP_RECEIPT_SCHEMA_ID,
        "generated_at": generated_at or _now(),
        "status": "PASS",
        "method_version": LOOKUP_METHOD_VERSION,
        "source_binding_sha256": source_binding_sha256,
        "lda_source_receipt_sha256": source_receipt_sha256,
        "lda_lobbyists": len(queue),
        "priority_counts": {key: priority_counts[key] for key in ("P0", "P1", "P2")},
        "covered_position_people": priority_counts["P0"] + priority_counts["P1"],
        "covered_position_people_with_year_token": year_hit_people,
        "covered_position_people_with_month_token": month_hit_people,
        "queue_conservation_pass": sum(priority_counts.values()) == len(queue),
        "identity_matches_asserted": 0,
        "canonical_identity_merges": 0,
        "collection_policy": "DISCOVERY_QUEUE_ONLY_NO_AUTOMATED_LEGISTORM_COLLECTION",
        "file": {
            "path": queue_path.name,
            "records": len(persisted_ids),
            "bytes": queue_path.stat().st_size,
            "sha256": sha256_file(queue_path),
            "encoding": "canonical-ndjson-gzip-mtime-0",
            "privacy_class": "restricted-machine-local",
        },
        "failures": [],
    }
    validate_instance(receipt, receipt_schema)
    _validate_record_semantics(receipt, receipt["schema_id"])
    _write_json(output / "employment_lookup_receipt.json", receipt)
    return receipt


def _canonical_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _parsed_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EmploymentHistoryError(f"invalid date-time: {value!r}") from exc
    if parsed.tzinfo is None:
        raise EmploymentHistoryError("date-time must carry an offset")
    return parsed.astimezone(timezone.utc)


def _datetime_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _latest_checkpoint_observation(
    rows: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    if not rows:
        raise EmploymentHistoryError("employment key has no observations")
    ordered = sorted(
        rows,
        key=lambda row: (
            _parsed_datetime(str(row["retrieved_at"])), str(row["observation_id"])
        ),
    )
    if len(ordered) > 1 and _parsed_datetime(
        str(ordered[-1]["retrieved_at"])
    ) == _parsed_datetime(str(ordered[-2]["retrieved_at"])):
        raise EmploymentHistoryError(
            "conflicting employment observations share one retrieval time"
        )
    return ordered[-1]


def _legistorm_subject_ids(profile_id: str, source_row_id: str) -> tuple[str, str, str]:
    person_subject_id = f"legistorm:person:{profile_id}"
    employment_key_digest = hashlib.sha256(
        f"{profile_id}\0{source_row_id}".encode("utf-8")
    ).hexdigest()
    employment_key = "legistorm:employment-key:" + employment_key_digest
    container_subject_id = "legistorm:container-candidate:" + employment_key_digest
    return person_subject_id, employment_key, container_subject_id


def _normalize_legistorm_capture(row: Mapping[str, Any]) -> dict[str, Any]:
    profile_id = str(row["profile_id"])
    profile_url = str(row["profile_url"])
    match = LEGISTORM_PROFILE_URL.fullmatch(profile_url)
    if match is None or match.group("profile_id") != profile_id:
        raise EmploymentHistoryError("LegiStorm profile URL/profile ID mismatch")
    retrieved = _parsed_datetime(str(row["retrieved_at"]))
    try:
        source_as_of = date.fromisoformat(str(row["source_as_of"]))
    except ValueError as exc:
        raise EmploymentHistoryError("invalid LegiStorm source_as_of") from exc
    if source_as_of > retrieved.date():
        raise EmploymentHistoryError("LegiStorm source_as_of exceeds retrieval date")

    start_precision = str(row["start_precision"])
    end_precision = str(row["end_precision"])
    normalized_start = row.get("normalized_start")
    normalized_end = row.get("normalized_end")
    asserted_current = bool(row["asserted_current"])
    end_semantics = str(row["source_end_semantics"])
    if normalized_start is None:
        if start_precision != "unknown":
            raise EmploymentHistoryError("unknown start requires unknown precision")
        start_earliest = start_latest = None
    else:
        start_earliest_date, start_latest_date = normalize_source_date(
            str(normalized_start), precision=start_precision, boundary="start"
        )
        start_earliest = start_earliest_date.isoformat()
        start_latest = start_latest_date.isoformat()

    if asserted_current:
        if end_semantics != "current" or normalized_end is not None:
            raise EmploymentHistoryError(
                "asserted-current capture needs current end semantics and no normalized end"
            )
        effective_end = source_as_of + timedelta(days=1)
        end_earliest = end_latest = effective_end.isoformat()
    elif end_semantics == "unknown":
        if normalized_end is not None or end_precision != "unknown":
            raise EmploymentHistoryError("unknown end has a normalized value or precision")
        end_earliest = end_latest = None
    else:
        if end_semantics not in {"inclusive", "exclusive"} or normalized_end is None:
            raise EmploymentHistoryError("dated end is missing semantics or normalized value")
        end_earliest_date, end_latest_date = normalize_source_date(
            str(normalized_end),
            precision=end_precision,
            boundary="end",
            end_semantics=end_semantics,
        )
        end_earliest = end_earliest_date.isoformat()
        end_latest = end_latest_date.isoformat()

    person_subject_id, employment_key, container_subject_id = _legistorm_subject_ids(
        profile_id, str(row["source_row_id"])
    )
    raw_payload = {
        "source_system_id": "legistorm",
        "profile_id": profile_id,
        "profile_url": profile_url,
        "retrieved_at": str(row["retrieved_at"]),
        "source_as_of": source_as_of.isoformat(),
        "profile_title_raw": str(row["profile_title_raw"]),
        "lda_source_person_id": row.get("lda_source_person_id"),
        "source_row_id": str(row["source_row_id"]),
        "raw_person_name": str(row["raw_person_name"]),
        "raw_employer": str(row["raw_employer"]),
        "raw_title": row.get("raw_title"),
        "raw_start": row.get("raw_start"),
        "normalized_start": normalized_start,
        "raw_end": row.get("raw_end"),
        "normalized_end": normalized_end,
        "start_precision": start_precision,
        "end_precision": end_precision,
        "source_end_semantics": end_semantics,
        "asserted_current": asserted_current,
    }
    source_record_sha256 = _canonical_digest(raw_payload)
    observation_id = "legistorm:employment-observation:" + source_record_sha256
    return {
        "schema_id": LEGISTORM_OBSERVATION_SCHEMA_ID,
        "observation_id": observation_id,
        **raw_payload,
        "person_subject_id": person_subject_id,
        "employment_key": employment_key,
        "container_subject_id": container_subject_id,
        "start_earliest": start_earliest,
        "start_latest": start_latest,
        "end_earliest": end_earliest,
        "end_latest": end_latest,
        "source_record_sha256": source_record_sha256,
    }


def _decision_core(
    capture: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    decision_kind: str,
) -> dict[str, Any]:
    if decision_kind == "PERSON_IDENTITY":
        source = capture["person_adjudication"]
        subject_id = observation["person_subject_id"]
        resolved_person_id = source.get("resolved_person_id")
        normalized_container_id = None
        container_kind = None
        member_bioguide_id = None
        organization_id = None
    elif decision_kind == "CONTAINER":
        source = capture["container_adjudication"]
        subject_id = observation["container_subject_id"]
        resolved_person_id = None
        normalized_container_id = source.get("normalized_container_id")
        container_kind = source.get("container_kind")
        member_bioguide_id = source.get("member_bioguide_id")
        organization_id = source.get("organization_id")
    else:  # pragma: no cover - private caller is fixed
        raise EmploymentHistoryError("invalid adjudication kind")
    status = str(source["status"])
    if status not in ADJUDICATION_STATUSES:
        raise EmploymentHistoryError(f"invalid adjudication status: {status}")
    reviewer_kind = str(source["reviewer_kind"])
    reviewer_id = source.get("reviewer_id")
    if status == "REVIEW_REQUIRED":
        if reviewer_kind != "UNASSIGNED" or reviewer_id is not None:
            raise EmploymentHistoryError("review-required decision is assigned")
    elif reviewer_kind == "UNASSIGNED" or not isinstance(reviewer_id, str) or not reviewer_id:
        raise EmploymentHistoryError("completed adjudication lacks a reviewer")
    if decision_kind == "PERSON_IDENTITY":
        if status == "ACCEPTED" and not resolved_person_id:
            raise EmploymentHistoryError("accepted person decision lacks resolved ID")
    else:
        if not isinstance(container_kind, str):
            raise EmploymentHistoryError("container decision lacks a container kind")
        if status == "ACCEPTED" and not normalized_container_id:
            raise EmploymentHistoryError("accepted container decision lacks normalized ID")
        if container_kind in HILL_KINDS and not member_bioguide_id:
            raise EmploymentHistoryError("Hill container decision lacks member Bioguide ID")
        if container_kind in INDUSTRY_KINDS and not organization_id:
            raise EmploymentHistoryError("industry container decision lacks organization ID")
    _parsed_datetime(str(source["recorded_at"]))
    return {
        "decision_kind": decision_kind,
        "subject_id": subject_id,
        "status": status,
        "resolved_person_id": resolved_person_id,
        "normalized_container_id": normalized_container_id,
        "container_kind": container_kind,
        "member_bioguide_id": member_bioguide_id,
        "organization_id": organization_id,
        "reviewer_kind": reviewer_kind,
        "reviewer_id": reviewer_id,
        "recorded_at": str(source["recorded_at"]),
        "rationale": str(source["rationale"]),
    }


def _decision_core_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "decision_kind",
            "subject_id",
            "status",
            "resolved_person_id",
            "normalized_container_id",
            "container_kind",
            "member_bioguide_id",
            "organization_id",
            "reviewer_kind",
            "reviewer_id",
            "recorded_at",
            "rationale",
        )
    }


def _merge_decision(
    decisions: dict[str, dict[str, Any]],
    core: Mapping[str, Any],
    evidence_observation_ids: Iterable[str],
) -> None:
    payload_sha256 = _canonical_digest(core)
    decision_id = "legistorm:adjudication:" + payload_sha256
    existing = decisions.get(decision_id)
    evidence = set(evidence_observation_ids)
    if existing is None:
        decisions[decision_id] = {
            "schema_id": EMPLOYMENT_DECISION_SCHEMA_ID,
            "decision_id": decision_id,
            **dict(core),
            "evidence_observation_ids": sorted(evidence),
            "decision_payload_sha256": payload_sha256,
            "supersedes_decision_id": None,
        }
        return
    if _decision_core_from_row(existing) != dict(core):
        raise EmploymentHistoryError("adjudication decision hash collision")
    existing["evidence_observation_ids"] = sorted(
        set(existing["evidence_observation_ids"]) | evidence
    )


def _chain_decisions(
    decisions: dict[str, dict[str, Any]], *, verify_existing: bool = False
) -> None:
    by_subject: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in decisions.values():
        expected_sha256 = _canonical_digest(_decision_core_from_row(row))
        if row["decision_payload_sha256"] != expected_sha256:
            raise EmploymentHistoryError("adjudication payload digest mismatch")
        if row["decision_id"] != "legistorm:adjudication:" + expected_sha256:
            raise EmploymentHistoryError("adjudication decision ID mismatch")
        by_subject[(row["decision_kind"], row["subject_id"])].append(row)
    for rows in by_subject.values():
        rows.sort(
            key=lambda row: (
                _parsed_datetime(str(row["recorded_at"])), row["decision_id"]
            )
        )
        for left, right in zip(rows, rows[1:]):
            if _parsed_datetime(str(left["recorded_at"])) == _parsed_datetime(
                str(right["recorded_at"])
            ):
                raise EmploymentHistoryError(
                    "conflicting adjudications share one recorded_at timestamp"
                )
        for index, row in enumerate(rows):
            expected_supersedes = (
                None if index == 0 else rows[index - 1]["decision_id"]
            )
            if verify_existing:
                if row.get("supersedes_decision_id") != expected_supersedes:
                    raise EmploymentHistoryError(
                        "adjudication supersession chain mismatch"
                    )
            else:
                row["supersedes_decision_id"] = expected_supersedes


def _active_decisions(
    decisions: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    active: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in decisions:
        key = (str(row["decision_kind"]), str(row["subject_id"]))
        prior = active.get(key)
        if prior is None or (
            _parsed_datetime(str(row["recorded_at"])), str(row["decision_id"])
        ) > (
            _parsed_datetime(str(prior["recorded_at"])), str(prior["decision_id"])
        ):
            active[key] = row
    return active


def _checkpoint_file_entry(path: Path, *, role: str, records: int) -> dict[str, Any]:
    return {
        "role": role,
        "path": path.name,
        "records": records,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "encoding": "canonical-ndjson-gzip-mtime-0",
        "privacy_class": "restricted-machine-local",
    }


def _verify_checkpoint_files(
    generation: Path, receipt: Mapping[str, Any]
) -> dict[str, Mapping[str, Any]]:
    roles = {str(row["role"]): row for row in receipt["files"]}
    if set(roles) != {
        "employment_observations", "adjudication_decisions", "employment_episodes"
    }:
        raise EmploymentHistoryError("checkpoint file roles are incomplete")
    for entry in roles.values():
        path = generation / str(entry["path"])
        if (
            not path.is_file()
            or path.stat().st_size != entry["bytes"]
            or sha256_file(path) != entry["sha256"]
        ):
            raise EmploymentHistoryError(f"checkpoint file binding failed: {entry['role']}")
        if sum(1 for _ in _rows(path)) != entry["records"]:
            raise EmploymentHistoryError(f"checkpoint row count failed: {entry['role']}")
    return roles


def _verified_canonical_episode_bindings(
    receipt_path: str | Path,
    *,
    expected_sha256: str,
) -> dict[str, tuple[str, str]]:
    """Verify a canonical checkpoint and return exact episode-payload bindings.

    Completeness callers must use this registry (or an equivalently verified
    registry from another canonical source) so episode-local status strings and
    provenance IDs cannot establish their own authority.
    """

    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise EmploymentHistoryError("canonical receipt SHA-256 is invalid")
    receipt_file = Path(receipt_path).resolve(strict=True)
    if sha256_file(receipt_file) != expected_sha256:
        raise EmploymentHistoryError("canonical receipt hash mismatch")
    receipt_schema = _read_json(
        SCHEMA_DIRECTORY / "legistorm_employment_checkpoint_receipt.schema.json"
    )
    receipt = _read_json(receipt_file)
    validate_instance(receipt, receipt_schema)
    _validate_record_semantics(receipt, receipt["schema_id"])
    generation = receipt_file.parent
    roles = _verify_checkpoint_files(generation, receipt)

    observation_schema = _read_json(
        SCHEMA_DIRECTORY / "legistorm_employment_observation.schema.json"
    )
    decision_schema = _read_json(
        SCHEMA_DIRECTORY / "employment_adjudication_decision.schema.json"
    )
    episode_schema = CANONICAL_EMPLOYMENT_EPISODE_SCHEMA
    observations = {
        str(row["observation_id"]): row
        for row in _rows(generation / str(roles["employment_observations"]["path"]))
    }
    decisions = {
        str(row["decision_id"]): row
        for row in _rows(generation / str(roles["adjudication_decisions"]["path"]))
    }
    episodes = list(
        _rows(generation / str(roles["employment_episodes"]["path"]))
    )
    if (
        len(observations) != receipt["observation_rows"]
        or len(decisions) != receipt["decision_rows"]
        or len(episodes) != receipt["episode_rows"]
    ):
        raise EmploymentHistoryError("canonical checkpoint ID cardinality drifted")
    for row in observations.values():
        validate_instance(row, observation_schema)
        _validate_record_semantics(row, row["schema_id"])
    for row in decisions.values():
        validate_instance(row, decision_schema)
        _validate_record_semantics(row, row["schema_id"])
    _chain_decisions(decisions, verify_existing=True)
    for row in decisions.values():
        evidence_ids = set(row["evidence_observation_ids"])
        if not evidence_ids or not evidence_ids.issubset(observations):
            raise EmploymentHistoryError("canonical decision evidence is dangling")
        subject_field = (
            "person_subject_id"
            if row["decision_kind"] == "PERSON_IDENTITY"
            else "container_subject_id"
        )
        if {
            str(observations[value][subject_field]) for value in evidence_ids
        } != {str(row["subject_id"])}:
            raise EmploymentHistoryError("canonical decision subject/evidence drifted")
    source_evidence_digest = _support_digest(
        row["source_record_sha256"] for row in observations.values()
    )
    decision_evidence_digest = _support_digest(
        row["decision_payload_sha256"] for row in decisions.values()
    )
    expected_generation_id = "legistorm-employment-" + _canonical_digest({
        "method_version": receipt["method_version"],
        "source_evidence_digest": source_evidence_digest,
        "decision_evidence_digest": decision_evidence_digest,
    })
    if (
        receipt["source_evidence_digest"] != source_evidence_digest
        or receipt["decision_evidence_digest"] != decision_evidence_digest
        or receipt["generation_id"] != expected_generation_id
    ):
        raise EmploymentHistoryError("canonical checkpoint evidence digest drifted")
    active = _active_decisions(decisions.values())
    status_counts = {
        kind: {
            status: sum(
                row["decision_kind"] == kind and row["status"] == status
                for row in decisions.values()
            )
            for status in sorted(ADJUDICATION_STATUSES)
        }
        for kind in ("PERSON_IDENTITY", "CONTAINER")
    }
    active_counts = {
        kind: {
            status: sum(
                row["decision_kind"] == kind and row["status"] == status
                for row in active.values()
            )
            for status in sorted(ADJUDICATION_STATUSES)
        }
        for kind in ("PERSON_IDENTITY", "CONTAINER")
    }
    event_times = [
        _parsed_datetime(str(row["retrieved_at"])) for row in observations.values()
    ] + [
        _parsed_datetime(str(row["recorded_at"])) for row in decisions.values()
    ]
    if (
        receipt["decision_status_counts"] != status_counts
        or receipt["active_decision_status_counts"] != active_counts
        or receipt["employment_keys"]
        != len({str(row["employment_key"]) for row in observations.values()})
        or receipt["generated_at"] != _datetime_text(max(event_times))
    ):
        raise EmploymentHistoryError("canonical checkpoint summary drifted")

    bindings: dict[str, tuple[str, str]] = {}
    episode_employment_keys: set[str] = set()
    eligibility_counts: Counter[str] = Counter()
    for episode in episodes:
        validate_instance(episode, episode_schema)
        _validate_record_semantics(episode, episode["schema_id"])
        identity = decisions.get(str(episode["identity_decision_id"]))
        container = decisions.get(str(episode["container_decision_id"]))
        support_ids = set(episode["support_observation_ids"])
        if identity is None or identity.get("decision_kind") != "PERSON_IDENTITY":
            raise EmploymentHistoryError("canonical episode identity decision is dangling")
        if container is None or container.get("decision_kind") != "CONTAINER":
            raise EmploymentHistoryError("canonical episode container decision is dangling")
        if not support_ids or not support_ids.issubset(observations):
            raise EmploymentHistoryError("canonical episode observation support is dangling")
        supports = [observations[value] for value in support_ids]
        source_people = {
            str(row["lda_source_person_id"]).strip().casefold()
            for row in supports
            if isinstance(row.get("lda_source_person_id"), str)
            and str(row["lda_source_person_id"]).strip()
        }
        if len(source_people) != 1:
            raise EmploymentHistoryError(
                "canonical episode lacks one source LDA person binding"
            )
        source_person_id = next(iter(source_people))
        employment_keys = {str(row["employment_key"]) for row in supports}
        if len(employment_keys) != 1:
            raise EmploymentHistoryError("canonical episode combines employment keys")
        employment_key = next(iter(employment_keys))
        expected_support_ids = {
            observation_id
            for observation_id, observation in observations.items()
            if str(observation["employment_key"]) == employment_key
        }
        if support_ids != expected_support_ids:
            raise EmploymentHistoryError(
                "canonical episode does not include every employment-key observation"
            )
        episode_employment_keys.add(employment_key)
        expected_episode_id = "legistorm:employment-episode:" + hashlib.sha256(
            employment_key.encode("utf-8")
        ).hexdigest()
        if episode["episode_id"] != expected_episode_id:
            raise EmploymentHistoryError("canonical episode ID is not source-bound")
        latest = _latest_checkpoint_observation(supports)
        if active.get(("PERSON_IDENTITY", latest["person_subject_id"])) is not identity:
            raise EmploymentHistoryError("canonical episode identity decision is not active")
        if active.get(("CONTAINER", latest["container_subject_id"])) is not container:
            raise EmploymentHistoryError("canonical episode container decision is not active")
        if not support_ids.issubset(set(identity["evidence_observation_ids"])):
            raise EmploymentHistoryError("identity decision does not bind episode support")
        if not support_ids.issubset(set(container["evidence_observation_ids"])):
            raise EmploymentHistoryError("container decision does not bind episode support")
        if (
            episode["identity_status"] != identity["status"]
            or episode["container_status"] != container["status"]
        ):
            raise EmploymentHistoryError("canonical episode decision status drifted")
        if identity["status"] == "ACCEPTED" and (
            episode["resolved_person_id"] != identity["resolved_person_id"]
        ):
            raise EmploymentHistoryError("canonical episode person resolution drifted")
        if container["status"] == "ACCEPTED" and any(
            episode[key] != container[key]
            for key in (
                "normalized_container_id", "container_kind",
                "member_bioguide_id", "organization_id",
            )
        ):
            raise EmploymentHistoryError("canonical episode container resolution drifted")
        expected_from_latest = {
            "title": latest["raw_title"],
            "start_earliest": latest["start_earliest"],
            "start_latest": latest["start_latest"],
            "end_earliest": latest["end_earliest"],
            "end_latest": latest["end_latest"],
            "start_precision": latest["start_precision"],
            "end_precision": latest["end_precision"],
            "asserted_current": latest["asserted_current"],
            "source_as_of": latest["source_as_of"],
        }
        if any(episode[key] != value for key, value in expected_from_latest.items()):
            raise EmploymentHistoryError("canonical episode source transform drifted")
        if episode["identity_status"] != "ACCEPTED":
            eligibility_counts["IDENTITY_BLOCKED"] += 1
        elif episode["container_status"] != "ACCEPTED":
            eligibility_counts["CONTAINER_BLOCKED"] += 1
        elif None in (
            episode["start_earliest"], episode["start_latest"],
            episode["end_earliest"], episode["end_latest"],
        ):
            eligibility_counts["INTERVAL_BLOCKED"] += 1
        else:
            eligibility_counts["PRIMARY_READY"] += 1
        episode_id = str(episode["episode_id"])
        if episode_id in bindings:
            raise EmploymentHistoryError("duplicate canonical episode ID")
        bindings[episode_id] = (
            hashlib.sha256(canonical_json_bytes(episode)).hexdigest(),
            source_person_id,
        )
    if episode_employment_keys != {
        str(row["employment_key"]) for row in observations.values()
    }:
        raise EmploymentHistoryError(
            "canonical checkpoint does not conserve observation employment keys"
        )
    expected_eligibility_counts = {
        key: eligibility_counts[key]
        for key in (
            "PRIMARY_READY", "IDENTITY_BLOCKED",
            "CONTAINER_BLOCKED", "INTERVAL_BLOCKED",
        )
    }
    if receipt["episode_eligibility_counts"] != expected_eligibility_counts:
        raise EmploymentHistoryError("canonical checkpoint eligibility summary drifted")
    return bindings


def load_canonical_employment_provenance(
    receipt_path: str | Path,
    *,
    expected_sha256: str,
) -> CanonicalEmploymentProvenance:
    """Load an immutable provenance registry from a hash-bound generation."""

    return CanonicalEmploymentProvenance(
        generation_receipt_path=str(Path(receipt_path).resolve(strict=True)),
        generation_receipt_sha256=expected_sha256,
    )


def _load_checkpoint_generation(
    output: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pointer_path = output / "legistorm_employment_current.json"
    if not pointer_path.is_file():
        return [], []
    pointer_schema = _read_json(SCHEMA_DIRECTORY / "current_pointer.schema.json")
    pointer = _read_json(pointer_path)
    validate_instance(pointer, pointer_schema)
    generation_id = str(pointer["generation_id"])
    if not re.fullmatch(r"legistorm-employment-[0-9a-f]{64}", generation_id):
        raise EmploymentHistoryError("invalid LegiStorm checkpoint generation ID")
    generation = (output / "generations" / generation_id).resolve()
    generations_root = (output / "generations").resolve()
    if generation.parent != generations_root:
        raise EmploymentHistoryError("checkpoint pointer escapes generations root")
    receipt_path = generation / "legistorm_employment_checkpoint_receipt.json"
    if not receipt_path.is_file() or sha256_file(receipt_path) != pointer["manifest_sha256"]:
        raise EmploymentHistoryError("checkpoint pointer/receipt binding failed")
    receipt_schema = _read_json(
        SCHEMA_DIRECTORY / "legistorm_employment_checkpoint_receipt.schema.json"
    )
    receipt = _read_json(receipt_path)
    validate_instance(receipt, receipt_schema)
    _validate_record_semantics(receipt, receipt["schema_id"])
    roles = _verify_checkpoint_files(generation, receipt)
    observations = list(_rows(generation / roles["employment_observations"]["path"]))
    decisions = list(_rows(generation / roles["adjudication_decisions"]["path"]))
    if len(observations) != roles["employment_observations"]["records"]:
        raise EmploymentHistoryError("checkpoint observation count drifted")
    if len(decisions) != roles["adjudication_decisions"]["records"]:
        raise EmploymentHistoryError("checkpoint decision count drifted")
    return observations, decisions


def build_legistorm_employment_checkpoint(
    captures_path: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    """Ingest local LegiStorm evidence into a deterministic resumable checkpoint.

    This function performs no network access.  Each input row is an authorized
    local transcription/capture with separate person and container adjudication
    objects.  Prior generations remain immutable; a content-addressed generation
    is written and the small pointer is replaced only after its receipt passes.
    """

    captures_source = Path(captures_path).resolve()
    output = Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    generations = output / "generations"
    generations.mkdir(parents=True, exist_ok=True)

    capture_schema = _read_json(
        SCHEMA_DIRECTORY / "legistorm_employment_capture.schema.json"
    )
    observation_schema = _read_json(
        SCHEMA_DIRECTORY / "legistorm_employment_observation.schema.json"
    )
    decision_schema = _read_json(
        SCHEMA_DIRECTORY / "employment_adjudication_decision.schema.json"
    )
    episode_schema = _read_json(SCHEMA_DIRECTORY / "employment_episode.schema.json")
    receipt_schema = _read_json(
        SCHEMA_DIRECTORY / "legistorm_employment_checkpoint_receipt.schema.json"
    )

    prior_observations, prior_decisions = _load_checkpoint_generation(output)
    observations: dict[str, dict[str, Any]] = {}
    for row in prior_observations:
        validate_instance(row, observation_schema)
        _validate_record_semantics(row, row["schema_id"])
        observations[str(row["observation_id"])] = row
    decisions: dict[str, dict[str, Any]] = {}
    for row in prior_decisions:
        validate_instance(row, decision_schema)
        _validate_record_semantics(row, row["schema_id"])
        decisions[str(row["decision_id"])] = row

    input_rows = list(_rows(captures_source))
    for capture in input_rows:
        validate_instance(capture, capture_schema)
        observation = _normalize_legistorm_capture(capture)
        validate_instance(observation, observation_schema)
        _validate_record_semantics(observation, observation["schema_id"])
        observation_id = str(observation["observation_id"])
        existing = observations.get(observation_id)
        if existing is not None and existing != observation:
            raise EmploymentHistoryError("LegiStorm observation hash collision")
        observations[observation_id] = observation
        for decision_kind in ("PERSON_IDENTITY", "CONTAINER"):
            _merge_decision(
                decisions,
                _decision_core(capture, observation, decision_kind=decision_kind),
                [observation_id],
            )
    _chain_decisions(decisions)

    for row in decisions.values():
        validate_instance(row, decision_schema)
        _validate_record_semantics(row, row["schema_id"])
    active = _active_decisions(decisions.values())
    observations_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations.values():
        observations_by_key[str(row["employment_key"])].append(row)

    episodes: list[dict[str, Any]] = []
    eligibility_counts: Counter[str] = Counter()
    for employment_key, supports in sorted(observations_by_key.items()):
        latest = _latest_checkpoint_observation(supports)
        person_decision = active[("PERSON_IDENTITY", latest["person_subject_id"])]
        container_decision = active[("CONTAINER", latest["container_subject_id"])]
        support_ids = sorted(row["observation_id"] for row in supports)
        person_id = (
            person_decision["resolved_person_id"]
            if person_decision["status"] == "ACCEPTED"
            else latest["person_subject_id"]
        )
        container_id = (
            container_decision["normalized_container_id"]
            if container_decision["status"] == "ACCEPTED"
            else latest["container_subject_id"]
        )
        episode_id = "legistorm:employment-episode:" + hashlib.sha256(
            employment_key.encode("utf-8")
        ).hexdigest()
        episode = {
            "schema_id": EPISODE_SCHEMA_ID,
            "episode_id": episode_id,
            "resolved_person_id": person_id,
            "normalized_container_id": container_id,
            "container_kind": container_decision["container_kind"],
            "member_bioguide_id": container_decision["member_bioguide_id"],
            "organization_id": container_decision["organization_id"],
            "title": latest["raw_title"],
            "start_earliest": latest["start_earliest"],
            "start_latest": latest["start_latest"],
            "end_earliest": latest["end_earliest"],
            "end_latest": latest["end_latest"],
            "start_precision": latest["start_precision"],
            "end_precision": latest["end_precision"],
            "asserted_current": latest["asserted_current"],
            "source_as_of": latest["source_as_of"],
            "identity_decision_id": person_decision["decision_id"],
            "identity_status": person_decision["status"],
            "container_decision_id": container_decision["decision_id"],
            "container_status": container_decision["status"],
            "support_observation_ids": support_ids,
            "support_digest": _support_digest(support_ids),
            "episode_status": "ACTIVE",
        }
        validate_instance(episode, episode_schema)
        _validate_record_semantics(episode, episode["schema_id"])
        if episode["identity_status"] != "ACCEPTED":
            eligibility_counts["IDENTITY_BLOCKED"] += 1
        elif episode["container_status"] != "ACCEPTED":
            eligibility_counts["CONTAINER_BLOCKED"] += 1
        elif None in (
            episode["start_earliest"], episode["start_latest"],
            episode["end_earliest"], episode["end_latest"],
        ):
            eligibility_counts["INTERVAL_BLOCKED"] += 1
        else:
            eligibility_counts["PRIMARY_READY"] += 1
        episodes.append(episode)

    source_evidence_digest = _support_digest(
        row["source_record_sha256"] for row in observations.values()
    )
    decision_evidence_digest = _support_digest(
        row["decision_payload_sha256"] for row in decisions.values()
    )
    generation_sha256 = _canonical_digest({
        "method_version": LEGISTORM_CHECKPOINT_METHOD_VERSION,
        "source_evidence_digest": source_evidence_digest,
        "decision_evidence_digest": decision_evidence_digest,
    })
    generation_id = "legistorm-employment-" + generation_sha256
    generation = generations / generation_id
    event_times = [
        _parsed_datetime(str(row["retrieved_at"])) for row in observations.values()
    ] + [
        _parsed_datetime(str(row["recorded_at"])) for row in decisions.values()
    ]
    if not event_times:
        raise EmploymentHistoryError("LegiStorm checkpoint has no evidence rows")
    generated_at = _datetime_text(max(event_times))

    if generation.exists():
        receipt_path = generation / "legistorm_employment_checkpoint_receipt.json"
        if not receipt_path.is_file():
            raise EmploymentHistoryError("existing checkpoint generation is incomplete")
        receipt = _read_json(receipt_path)
        validate_instance(receipt, receipt_schema)
        _validate_record_semantics(receipt, receipt["schema_id"])
        if receipt["generation_id"] != generation_id:
            raise EmploymentHistoryError("existing checkpoint generation ID drifted")
        if (
            receipt["source_evidence_digest"] != source_evidence_digest
            or receipt["decision_evidence_digest"] != decision_evidence_digest
            or receipt["observation_rows"] != len(observations)
            or receipt["decision_rows"] != len(decisions)
            or receipt["episode_rows"] != len(episodes)
        ):
            raise EmploymentHistoryError("existing checkpoint generation content drifted")
        _verify_checkpoint_files(generation, receipt)
    else:
        staging = Path(tempfile.mkdtemp(prefix=f".{generation_id}.tmp-", dir=generations))
        try:
            observations_path = staging / "legistorm_employment_observations.ndjson.gz"
            decisions_path = staging / "legistorm_adjudication_decisions.ndjson.gz"
            episodes_path = staging / "legistorm_employment_episodes.ndjson.gz"
            _write_sorted_gzip(
                observations_path, observations.values(),
                key=lambda row: row["observation_id"],
            )
            _write_sorted_gzip(
                decisions_path, decisions.values(), key=lambda row: row["decision_id"]
            )
            _write_sorted_gzip(
                episodes_path, episodes, key=lambda row: row["episode_id"]
            )
            status_counts = {
                kind: {
                    status: sum(
                        row["decision_kind"] == kind and row["status"] == status
                        for row in decisions.values()
                    )
                    for status in sorted(ADJUDICATION_STATUSES)
                }
                for kind in ("PERSON_IDENTITY", "CONTAINER")
            }
            active_counts = {
                kind: {
                    status: sum(
                        row["decision_kind"] == kind and row["status"] == status
                        for row in active.values()
                    )
                    for status in sorted(ADJUDICATION_STATUSES)
                }
                for kind in ("PERSON_IDENTITY", "CONTAINER")
            }
            files = [
                _checkpoint_file_entry(
                    observations_path,
                    role="employment_observations",
                    records=len(observations),
                ),
                _checkpoint_file_entry(
                    decisions_path,
                    role="adjudication_decisions",
                    records=len(decisions),
                ),
                _checkpoint_file_entry(
                    episodes_path,
                    role="employment_episodes",
                    records=len(episodes),
                ),
            ]
            receipt = {
                "schema_id": LEGISTORM_CHECKPOINT_RECEIPT_SCHEMA_ID,
                "generated_at": generated_at,
                "status": "PASS",
                "method_version": LEGISTORM_CHECKPOINT_METHOD_VERSION,
                "generation_id": generation_id,
                "source_system_id": "legistorm",
                "collection_policy": "LOCAL_AUTHORIZED_EVIDENCE_ONLY_NO_NETWORK_ACCESS",
                "source_evidence_digest": source_evidence_digest,
                "decision_evidence_digest": decision_evidence_digest,
                "observation_rows": len(observations),
                "employment_keys": len(observations_by_key),
                "decision_rows": len(decisions),
                "decision_status_counts": status_counts,
                "active_decision_status_counts": active_counts,
                "episode_rows": len(episodes),
                "episode_eligibility_counts": {
                    key: eligibility_counts[key]
                    for key in (
                        "PRIMARY_READY", "IDENTITY_BLOCKED",
                        "CONTAINER_BLOCKED", "INTERVAL_BLOCKED",
                    )
                },
                "person_container_adjudication_independent": True,
                "canonical_identity_merges": 0,
                "conservation_pass": (
                    len(episodes) == len(observations_by_key)
                    and sum(eligibility_counts.values()) == len(episodes)
                ),
                "files": files,
                "failures": [],
            }
            validate_instance(receipt, receipt_schema)
            _validate_record_semantics(receipt, receipt["schema_id"])
            _write_json(
                staging / "legistorm_employment_checkpoint_receipt.json", receipt
            )
            os.replace(staging, generation)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    receipt_path = generation / "legistorm_employment_checkpoint_receipt.json"
    pointer = {
        "schema_id": "staffer-network-current-pointer-v1",
        "generation_id": generation_id,
        "manifest_sha256": sha256_file(receipt_path),
    }
    validate_instance(
        pointer, _read_json(SCHEMA_DIRECTORY / "current_pointer.schema.json")
    )
    _write_json(output / "legistorm_employment_current.json", pointer)
    return receipt


def _parse_date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _date_text(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def interval_from_episode(row: Mapping[str, Any]) -> UncertainInterval:
    interval = UncertainInterval(
        _parse_date(row.get("start_earliest")),
        _parse_date(row.get("start_latest")),
        _parse_date(row.get("end_earliest")),
        _parse_date(row.get("end_latest")),
        str(row.get("start_precision", "unknown")),
        str(row.get("end_precision", "unknown")),
        False,
    )
    interval.validate()
    return interval


def normalize_source_date(
    raw: str,
    *,
    precision: str,
    boundary: str,
    end_semantics: str = "unknown",
) -> tuple[date, date]:
    """Normalize a source date into day-level uncertainty bounds.

    Returned end bounds are exclusive.  Month/year end values require an
    explicit inclusive/exclusive source convention.
    """

    if boundary not in {"start", "end"}:
        raise EmploymentHistoryError("boundary must be start or end")
    if precision not in {"day", "month", "year"}:
        raise EmploymentHistoryError("dated normalization requires day/month/year")
    if boundary == "end" and end_semantics not in {"inclusive", "exclusive"}:
        raise EmploymentHistoryError("dated end requires inclusive/exclusive semantics")
    if precision == "day":
        first = date.fromisoformat(raw)
        last = first
    elif precision == "month":
        year, month = (int(part) for part in raw.split("-"))
        first = date(year, month, 1)
        next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        last = next_month - timedelta(days=1)
    else:
        year = int(raw)
        first = date(year, 1, 1)
        last = date(year, 12, 31)
    if boundary == "start":
        return first, last
    if end_semantics == "inclusive":
        return first + timedelta(days=1), last + timedelta(days=1)
    return first, last


Range = tuple[date, date]


def union_ranges(ranges: Iterable[Range]) -> list[Range]:
    ordered = sorted((start, end) for start, end in ranges if end > start)
    merged: list[Range] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def intersect_ranges(left: Sequence[Range], right: Sequence[Range]) -> list[Range]:
    intersections: list[Range] = []
    i = j = 0
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if end > start:
            intersections.append((start, end))
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return union_ranges(intersections)


def range_days(ranges: Iterable[Range]) -> int:
    return sum((end - start).days for start, end in union_ranges(ranges))


def duration_weight(days: int, *, tau_days: float = 365.2425) -> float:
    if days < 0 or not math.isfinite(tau_days) or tau_days <= 0:
        raise EmploymentHistoryError("duration transform inputs are invalid")
    return 1.0 - math.exp(-days / tau_days)


def episode_core(row: Mapping[str, Any]) -> Range | None:
    interval = interval_from_episode(row)
    if None in (interval.start_latest, interval.end_earliest):
        return None
    assert interval.start_latest is not None and interval.end_earliest is not None
    return (
        (interval.start_latest, interval.end_earliest)
        if interval.end_earliest > interval.start_latest else None
    )


def episode_envelope(row: Mapping[str, Any]) -> Range | None:
    interval = interval_from_episode(row)
    if None in (interval.start_earliest, interval.end_latest):
        return None
    assert interval.start_earliest is not None and interval.end_latest is not None
    return (
        (interval.start_earliest, interval.end_latest)
        if interval.end_latest > interval.start_earliest else None
    )


def co_tenure_bounds(
    left: Iterable[Mapping[str, Any]],
    right: Iterable[Mapping[str, Any]],
) -> tuple[int, int, list[Range], list[Range]]:
    left_rows, right_rows = list(left), list(right)
    left_core = union_ranges(value for row in left_rows if (value := episode_core(row)))
    right_core = union_ranges(value for row in right_rows if (value := episode_core(row)))
    left_envelope = union_ranges(
        value for row in left_rows if (value := episode_envelope(row))
    )
    right_envelope = union_ranges(
        value for row in right_rows if (value := episode_envelope(row))
    )
    core_overlap = intersect_ranges(left_core, right_core)
    envelope_overlap = intersect_ranges(left_envelope, right_envelope)
    guaranteed = range_days(core_overlap)
    possible = range_days(envelope_overlap)
    if guaranteed > possible:
        raise EmploymentHistoryError("guaranteed co-tenure exceeds possible co-tenure")
    return guaranteed, possible, core_overlap, envelope_overlap


def transition_gap_bounds(
    hill: Mapping[str, Any], industry: Mapping[str, Any]
) -> tuple[str, int | None, int | None]:
    h = interval_from_episode(hill)
    z = interval_from_episode(industry)
    if None in (h.end_earliest, h.end_latest, z.start_earliest, z.start_latest):
        return "INTERVAL_BLOCKED", None, None
    assert h.end_earliest and h.end_latest and z.start_earliest and z.start_latest
    guaranteed = h.end_latest <= z.start_earliest
    possible = h.end_earliest <= z.start_latest
    gap_min = max(0, (z.start_earliest - h.end_latest).days)
    gap_max = max(0, (z.start_latest - h.end_earliest).days)
    if gap_min > gap_max:
        raise EmploymentHistoryError("transition gap bounds are inverted")
    return (
        "GUARANTEED" if guaranteed else ("POSSIBLE_ONLY" if possible else "WRONG_ORDER"),
        gap_min,
        gap_max,
    )


def _support_digest(ids: Iterable[str]) -> str:
    values = sorted(set(ids))
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()


def _range_rows(ranges: Iterable[Range]) -> list[dict[str, str]]:
    return [
        {"start": start.isoformat(), "end_exclusive": end.isoformat()}
        for start, end in ranges
    ]


def build_temporal_sidecar(
    episodes_path: str | Path,
    output_root: str | Path,
    *,
    as_of: str,
    generated_at: str | None = None,
    tau_days: float = 365.2425,
    gap_half_life_days: float = 730.485,
) -> dict[str, Any]:
    """Derive conservative co-tenure and revolving-door sidecar artifacts."""

    source = Path(episodes_path)
    output = Path(output_root)
    if output.exists() and any(output.iterdir()):
        raise EmploymentHistoryError(f"output root is nonempty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    build_as_of = date.fromisoformat(as_of)
    if not math.isfinite(gap_half_life_days) or gap_half_life_days <= 0:
        raise EmploymentHistoryError("gap half-life must be positive and finite")
    episode_schema = _read_json(SCHEMA_DIRECTORY / "employment_episode.schema.json")
    path_schema = _read_json(SCHEMA_DIRECTORY / "temporal_path.schema.json")
    edge_schema = _read_json(SCHEMA_DIRECTORY / "temporal_edge.schema.json")
    receipt_schema = _read_json(SCHEMA_DIRECTORY / "temporal_build_receipt.schema.json")
    episodes = list(_rows(source))
    seen_ids: set[str] = set()
    for row in episodes:
        validate_instance(row, episode_schema)
        _validate_record_semantics(row, row["schema_id"])
        if row["episode_id"] in seen_ids:
            raise EmploymentHistoryError("duplicate employment episode_id")
        seen_ids.add(row["episode_id"])
        interval = interval_from_episode(row)
        if interval.end_latest and interval.end_latest > build_as_of + timedelta(days=1):
            raise EmploymentHistoryError("episode extends beyond build as_of")

    eligible = [
        row for row in episodes
        if row["episode_status"] == "ACTIVE"
        and row["identity_status"] == "ACCEPTED"
        and row["container_status"] == "ACCEPTED"
    ]
    by_container_person: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        if row["container_kind"] in CO_TENURE_KINDS:
            by_container_person[(row["normalized_container_id"], row["resolved_person_id"])].append(row)

    container_people: dict[str, list[str]] = defaultdict(list)
    for container, person in by_container_person:
        container_people[container].append(person)

    temporal_paths: list[dict[str, Any]] = []
    pair_core_ranges: dict[tuple[str, str], list[Range]] = defaultdict(list)
    pair_envelope_ranges: dict[tuple[str, str], list[Range]] = defaultdict(list)
    co_statuses: Counter[str] = Counter()
    for container in sorted(container_people):
        people = sorted(set(container_people[container]))
        for left, right in combinations(people, 2):
            guaranteed, possible, core_ranges, envelope_ranges = co_tenure_bounds(
                by_container_person[(container, left)],
                by_container_person[(container, right)],
            )
            status = "GUARANTEED" if guaranteed else ("POSSIBLE_ONLY" if possible else "ZERO_OVERLAP")
            co_statuses[status] += 1
            support = sorted({
                support_id
                for row in (
                    by_container_person[(container, left)]
                    + by_container_person[(container, right)]
                )
                for support_id in row["support_observation_ids"]
            })
            path_id = "temporal:path:" + hashlib.sha256(
                f"CO_TENURE\0{left}\0{right}\0{container}".encode("utf-8")
            ).hexdigest()
            temporal_paths.append({
                "schema_id": TEMPORAL_PATH_SCHEMA_ID,
                "path_id": path_id,
                "predicate": "CO_TENURE",
                "status": status,
                "person_id": left,
                "other_person_id": right,
                "member_bioguide_id": None,
                "organization_id": container,
                "organization_role": "employment_container",
                "guaranteed_days": guaranteed,
                "possible_days": possible,
                "gap_min_days": None,
                "gap_max_days": None,
                "primary_weight": duration_weight(guaranteed, tau_days=tau_days) if guaranteed else 0.0,
                "possible_weight": duration_weight(possible, tau_days=tau_days) if possible else 0.0,
                "guaranteed_ranges": _range_rows(core_ranges),
                "possible_ranges": _range_rows(envelope_ranges),
                "support_observation_ids": support,
                "support_digest": _support_digest(support),
            })
            pair = (left, right)
            pair_core_ranges[pair].extend(core_ranges)
            pair_envelope_ranges[pair].extend(envelope_ranges)

    co_tenure_path_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in temporal_paths:
        if row["predicate"] == "CO_TENURE":
            co_tenure_path_ids[
                (row["person_id"], row["other_person_id"])
            ].append(row["path_id"])
    temporal_edges: list[dict[str, Any]] = []
    for (left, right) in sorted(pair_envelope_ranges):
        guaranteed = range_days(pair_core_ranges[(left, right)])
        possible = range_days(pair_envelope_ranges[(left, right)])
        if guaranteed <= 0:
            continue
        supporting_paths = sorted(co_tenure_path_ids[(left, right)])
        edge_id = "temporal:edge:" + hashlib.sha256(
            f"CO_TENURE\0{left}\0{right}".encode("utf-8")
        ).hexdigest()
        temporal_edges.append({
            "schema_id": TEMPORAL_EDGE_SCHEMA_ID,
            "edge_id": edge_id,
            "predicate": "CO_TENURE",
            "subject_id": left,
            "object_id": right,
            "organization_role": "employment_container",
            "raw_weight": duration_weight(guaranteed, tau_days=tau_days),
            "possible_weight": duration_weight(possible, tau_days=tau_days),
            "guaranteed_days": guaranteed,
            "possible_days": possible,
            "contributing_person_count": None,
            "gap_decay_lower": None,
            "gap_decay_upper": None,
            "support_path_ids": supporting_paths,
            "support_digest": _support_digest(supporting_paths),
        })

    by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_person[row["resolved_person_id"]].append(row)
    revolving_candidates: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    revolving_statuses: Counter[str] = Counter()
    for person, person_rows in sorted(by_person.items()):
        hills = [row for row in person_rows if row["container_kind"] in HILL_KINDS]
        industries = [row for row in person_rows if row["container_kind"] in INDUSTRY_KINDS]
        for hill in hills:
            member = hill.get("member_bioguide_id")
            if not isinstance(member, str) or not member:
                continue
            for industry in industries:
                organization = industry.get("organization_id")
                if not isinstance(organization, str) or not organization:
                    continue
                role = (
                    "registrant_employer"
                    if industry["container_kind"] == "registrant"
                    else "client_activity"
                )
                status, gap_min, gap_max = transition_gap_bounds(hill, industry)
                predicate = (
                    "FORMER_STAFFER_EMPLOYED_BY_REGISTRANT"
                    if role == "registrant_employer"
                    else "FORMER_STAFFER_NAMED_ON_ACTIVITY_FOR_CLIENT"
                )
                revolving_candidates[(person, member, organization, role)].append({
                    "hill": hill,
                    "industry": industry,
                    "status": status,
                    "gap_min": gap_min,
                    "gap_max": gap_max,
                    "predicate": predicate,
                })

    qualified_by_edge: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for key, candidates in sorted(revolving_candidates.items()):
        person, member, organization, role = key
        guaranteed = [row for row in candidates if row["status"] == "GUARANTEED"]
        possible = [row for row in candidates if row["status"] == "POSSIBLE_ONLY"]
        blocked = [row for row in candidates if row["status"] == "INTERVAL_BLOCKED"]
        wrong = [row for row in candidates if row["status"] == "WRONG_ORDER"]
        if guaranteed:
            chosen = min(
                guaranteed,
                key=lambda row: (
                    row["gap_max"], row["gap_min"],
                    row["industry"]["episode_id"], row["hill"]["episode_id"],
                ),
            )
        elif possible:
            chosen = min(
                possible,
                key=lambda row: (
                    row["gap_max"], row["gap_min"],
                    row["industry"]["episode_id"], row["hill"]["episode_id"],
                ),
            )
        elif blocked:
            chosen = min(
                blocked,
                key=lambda row: (
                    row["industry"]["episode_id"], row["hill"]["episode_id"]
                ),
            )
        elif wrong:
            chosen = min(
                wrong,
                key=lambda row: (
                    row["industry"]["episode_id"], row["hill"]["episode_id"]
                ),
            )
        else:
            raise EmploymentHistoryError("unsupported revolving transition status")
        revolving_statuses[chosen["status"]] += 1
        support = sorted(set(
            chosen["hill"]["support_observation_ids"]
            + chosen["industry"]["support_observation_ids"]
        ))
        gap_min, gap_max = chosen["gap_min"], chosen["gap_max"]
        if chosen["status"] == "INTERVAL_BLOCKED":
            if gap_min is not None or gap_max is not None:
                raise EmploymentHistoryError("interval-blocked path has gap bounds")
            lower = upper = 0.0
        else:
            assert gap_min is not None and gap_max is not None
            lower = 2.0 ** (-gap_max / gap_half_life_days) if chosen["status"] == "GUARANTEED" else 0.0
            upper = 2.0 ** (-gap_min / gap_half_life_days) if chosen["status"] == "GUARANTEED" else 0.0
        path_id = "temporal:path:" + hashlib.sha256(
            f"{chosen['predicate']}\0{person}\0{member}\0{organization}".encode("utf-8")
        ).hexdigest()
        path = {
            "schema_id": TEMPORAL_PATH_SCHEMA_ID,
            "path_id": path_id,
            "predicate": chosen["predicate"],
            "status": chosen["status"],
            "person_id": person,
            "other_person_id": None,
            "member_bioguide_id": member,
            "organization_id": organization,
            "organization_role": role,
            "guaranteed_days": None,
            "possible_days": None,
            "gap_min_days": gap_min,
            "gap_max_days": gap_max,
            "primary_weight": 1.0 if chosen["status"] == "GUARANTEED" else 0.0,
            "possible_weight": upper,
            "guaranteed_ranges": [],
            "possible_ranges": [],
            "support_observation_ids": support,
            "support_digest": _support_digest(support),
        }
        temporal_paths.append(path)
        if chosen["status"] == "GUARANTEED":
            qualified_by_edge[(member, organization, chosen["predicate"])].append({
                "path": path, "lower": lower, "upper": upper, "person": person
            })

    for (member, organization, predicate), contributions in sorted(qualified_by_edge.items()):
        people = {row["person"] for row in contributions}
        if len(people) != len(contributions):
            raise EmploymentHistoryError("person contributed twice to a revolving edge")
        path_ids = sorted(row["path"]["path_id"] for row in contributions)
        edge_id = "temporal:edge:" + hashlib.sha256(
            f"{predicate}\0{member}\0{organization}".encode("utf-8")
        ).hexdigest()
        temporal_edges.append({
            "schema_id": TEMPORAL_EDGE_SCHEMA_ID,
            "edge_id": edge_id,
            "predicate": predicate,
            "subject_id": member,
            "object_id": organization,
            "organization_role": (
                "registrant_employer"
                if predicate == "FORMER_STAFFER_EMPLOYED_BY_REGISTRANT"
                else "client_activity"
            ),
            "raw_weight": float(len(people)),
            "possible_weight": float(len(people)),
            "guaranteed_days": None,
            "possible_days": None,
            "contributing_person_count": len(people),
            "gap_decay_lower": sum(row["lower"] for row in contributions),
            "gap_decay_upper": sum(row["upper"] for row in contributions),
            "support_path_ids": path_ids,
            "support_digest": _support_digest(path_ids),
        })

    for row in temporal_paths:
        validate_instance(row, path_schema)
        _validate_record_semantics(row, row["schema_id"])
    for row in temporal_edges:
        validate_instance(row, edge_schema)
        _validate_record_semantics(row, row["schema_id"])
    paths_path = output / "temporal_paths.ndjson.gz"
    edges_path = output / "temporal_edges.ndjson.gz"
    _write_sorted_gzip(paths_path, temporal_paths, key=lambda row: row["path_id"])
    _write_sorted_gzip(edges_path, temporal_edges, key=lambda row: row["edge_id"])
    file_rows = [
        {
            "role": "employment_episodes",
            "path": source.name,
            "records": len(episodes),
            "bytes": source.stat().st_size,
            "sha256": sha256_file(source),
        },
        {
            "role": "temporal_paths",
            "path": paths_path.name,
            "records": len(temporal_paths),
            "bytes": paths_path.stat().st_size,
            "sha256": sha256_file(paths_path),
        },
        {
            "role": "temporal_edges",
            "path": edges_path.name,
            "records": len(temporal_edges),
            "bytes": edges_path.stat().st_size,
            "sha256": sha256_file(edges_path),
        },
    ]
    co_keys = sum(co_statuses.values())
    revolving_keys = sum(revolving_statuses.values())
    receipt = {
        "schema_id": TEMPORAL_RECEIPT_SCHEMA_ID,
        "generated_at": generated_at or _now(),
        "as_of": as_of,
        "status": "PASS",
        "method_version": TEMPORAL_METHOD_VERSION,
        "tau_days": tau_days,
        "gap_half_life_days": gap_half_life_days,
        "episode_rows": len(episodes),
        "eligible_episode_rows": len(eligible),
        "ineligible_episode_rows": len(episodes) - len(eligible),
        "co_tenure_candidate_keys": co_keys,
        "co_tenure_status_counts": {
            key: co_statuses[key]
            for key in ("GUARANTEED", "POSSIBLE_ONLY", "ZERO_OVERLAP")
        },
        "revolving_candidate_keys": revolving_keys,
        "revolving_status_counts": {
            key: revolving_statuses[key]
            for key in (
                "GUARANTEED", "POSSIBLE_ONLY", "WRONG_ORDER", "INTERVAL_BLOCKED"
            )
        },
        "primary_co_tenure_edges": sum(
            row["predicate"] == "CO_TENURE" for row in temporal_edges
        ),
        "primary_revolving_edges": sum(
            row["predicate"] != "CO_TENURE" for row in temporal_edges
        ),
        "unresolved_identities_in_primary_edges": 0,
        "undated_temporal_weights": 0,
        "self_loops": 0,
        "files": file_rows,
        "failures": [],
    }
    validate_instance(receipt, receipt_schema)
    _validate_record_semantics(receipt, receipt["schema_id"])
    _write_json(output / "temporal_build_receipt.json", receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    legistorm = subparsers.add_parser("ingest-legistorm")
    legistorm.add_argument("--captures", required=True)
    legistorm.add_argument("--output-root", required=True)
    lookup = subparsers.add_parser("build-lookup")
    lookup.add_argument("--lda-corpus-root", required=True)
    lookup.add_argument("--output-root", required=True)
    lookup.add_argument("--generated-at")
    temporal = subparsers.add_parser("build-temporal")
    temporal.add_argument("--episodes", required=True)
    temporal.add_argument("--output-root", required=True)
    temporal.add_argument("--as-of", required=True)
    temporal.add_argument("--generated-at")
    temporal.add_argument("--tau-days", type=float, default=365.2425)
    temporal.add_argument("--gap-half-life-days", type=float, default=730.485)
    completeness = subparsers.add_parser("audit-completeness")
    completeness.add_argument("--records", required=True, nargs="+")
    completeness.add_argument("--output-root", required=True)
    completeness.add_argument("--canonical-generation-receipt")
    completeness.add_argument("--canonical-generation-receipt-sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "ingest-legistorm":
            receipt = build_legistorm_employment_checkpoint(
                args.captures, args.output_root
            )
        elif args.command == "build-lookup":
            receipt = build_lookup_queue(
                args.lda_corpus_root,
                args.output_root,
                generated_at=args.generated_at,
            )
        elif args.command == "build-temporal":
            receipt = build_temporal_sidecar(
                args.episodes,
                args.output_root,
                as_of=args.as_of,
                generated_at=args.generated_at,
                tau_days=args.tau_days,
                gap_half_life_days=args.gap_half_life_days,
            )
        else:
            if bool(args.canonical_generation_receipt) != bool(
                args.canonical_generation_receipt_sha256
            ):
                raise EmploymentHistoryError(
                    "canonical receipt path and SHA-256 must be supplied together"
                )
            provenance = (
                load_canonical_employment_provenance(
                    args.canonical_generation_receipt,
                    expected_sha256=args.canonical_generation_receipt_sha256,
                )
                if args.canonical_generation_receipt
                else None
            )
            receipt = audit_history_corpus(
                args.records,
                args.output_root,
                canonical_provenance=provenance,
            )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CanonicalEmploymentProvenance",
    "EmploymentHistoryError",
    "audit_history_completeness",
    "audit_history_corpus",
    "build_legistorm_employment_checkpoint",
    "build_lookup_queue",
    "build_temporal_sidecar",
    "co_tenure_bounds",
    "load_canonical_employment_provenance",
    "duration_weight",
    "episode_core",
    "episode_envelope",
    "intersect_ranges",
    "normalize_source_date",
    "range_days",
    "transition_gap_bounds",
    "union_ranges",
]
