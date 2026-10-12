# -*- coding: utf-8 -*-
"""Materialize a support-backed singleton canonical fact plane.

The first canonical generation deliberately creates one canonical entity for
each source entity.  Cross-source name matches remain reversible candidate
links; they never merge entities.  This makes canonical facts and graph inputs
available without turning exact-name equality into identity truth.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Iterator, Mapping
import unicodedata

import yaml

from .source_manifest import canonical_json_bytes, sha256_file
from .schema_validation import validate_instance


ENTITY_SCHEMA_ID = "staffer-network-canonical-entity-v1"
FACT_SCHEMA_ID = "staffer-network-canonical-fact-v1"
IDENTITY_LINK_SCHEMA_ID = "staffer-network-canonical-identity-link-v1"
RECEIPT_SCHEMA_ID = "staffer-network-canonical-generation-receipt-v1"


class CanonicalFactsError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _canonical_id(kind: str, source_system_id: str, source_entity_id: str) -> str:
    token = hashlib.sha256(
        f"{source_system_id}\0{source_entity_id}".encode("utf-8")
    ).hexdigest()
    return f"canonical:{kind}:{token}"


def _fact_id(
    predicate: str,
    subject_id: str,
    object_id: str | None,
    literal: Any,
    source_system_id: str,
    source_assertion_id: str,
) -> str:
    return "canonical:fact:" + _digest(
        {
            "literal": literal,
            "object_entity_id": object_id,
            "predicate": predicate,
            "source_assertion_id": source_assertion_id,
            "source_system_id": source_system_id,
            "subject_entity_id": subject_id,
        }
    )


def _normalize_name(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode(
        "ascii", "ignore"
    ).decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


@contextmanager
def _gzip_writer(path: Path) -> Iterator[gzip.GzipFile]:
    raw = path.open("wb")
    compressed = gzip.GzipFile(
        # Level 6 preserves deterministic mtime-free gzip while materially
        # reducing wall time for the six-million-row canonical fact stream.
        filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0
    )
    try:
        yield compressed
    finally:
        compressed.close()
        raw.close()


def _write_row(stream: gzip.GzipFile, row: Mapping[str, Any]) -> None:
    stream.write(canonical_json_bytes(row))


def _read_rows(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise CanonicalFactsError(f"blank row: {path}:{line_number}")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise CanonicalFactsError(f"non-object row: {path}:{line_number}")
            yield value


def _latest_term(person: Mapping[str, Any]) -> Mapping[str, Any] | None:
    terms = person.get("terms")
    if not isinstance(terms, list) or not terms:
        return None
    return max(
        (term for term in terms if isinstance(term, Mapping)),
        key=lambda term: str(term.get("start") or ""),
        default=None,
    )


def _legislator_index(
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    people = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(people, list):
        raise CanonicalFactsError("legislator reference is not a list")
    name_index: dict[str, set[str]] = defaultdict(set)
    accepted: list[dict[str, Any]] = []
    for person in people:
        if not isinstance(person, Mapping):
            continue
        identifiers = person.get("id")
        names = person.get("name")
        if not isinstance(identifiers, Mapping) or not isinstance(names, Mapping):
            continue
        bioguide = identifiers.get("bioguide")
        if not isinstance(bioguide, str) or not bioguide:
            continue
        variants = {
            str(names.get("official_full") or "").strip(),
            " ".join(
                str(names.get(key) or "").strip()
                for key in ("first", "middle", "last", "suffix")
            ).strip(),
            " ".join(
                str(names.get(key) or "").strip() for key in ("first", "last")
            ).strip(),
            " ".join(
                str(names.get(key) or "").strip() for key in ("usual", "last")
            ).strip(),
        }
        for variant in variants:
            if variant:
                name_index[_normalize_name(variant)].add(bioguide)
        accepted.append(dict(person))
    return accepted, name_index


_POLITICO_MEMBER_ROLE = re.compile(
    r" for (?:Rep|Sen)\. (.*?)(Democratic|Republican|Independent|Unknown)"
    r"([A-Z]{2})(?:-(\d{2}))?$"
)

_EMAIL_CONTACT = re.compile(
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+[.][A-Z]{2,}", re.IGNORECASE
)
_PHONE_CONTACT = re.compile(
    r"(?<!\d)(?:[+]?[1][-. ]?)?[(]?\d{3}[)]?[-. ]\d{3}[-. ]\d{4}(?!\d)"
)


def _sanitize_contact_text(value: str) -> tuple[str | None, int]:
    cleaned, email_count = _EMAIL_CONTACT.subn("", value)
    cleaned, phone_count = _PHONE_CONTACT.subn("", cleaned)
    cleaned = re.sub(r"[(]\s*[)]", "", cleaned)
    cleaned = re.sub(r"\s+([,;:)])", r"\1", cleaned)
    cleaned = re.sub(r"([(])\s+", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t,;/")
    return (cleaned or None), email_count + phone_count


def _sanitize_free_text(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return _sanitize_contact_text(value)
    if isinstance(value, list):
        output: list[Any] = []
        redactions = 0
        for item in value:
            sanitized, count = _sanitize_free_text(item)
            redactions += count
            if sanitized is not None:
                output.append(sanitized)
        return output, redactions
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        redactions = 0
        for key, item in value.items():
            sanitized, count = _sanitize_free_text(item)
            redactions += count
            output[str(key)] = sanitized
        return output, redactions
    return value, 0


def _role_member_bioguide(
    role_text: str, name_index: Mapping[str, set[str]]
) -> str | None:
    match = _POLITICO_MEMBER_ROLE.search(role_text)
    if match is None:
        return None
    candidates = name_index.get(_normalize_name(match.group(1)), set())
    return next(iter(candidates)) if len(candidates) == 1 else None


def _little_kind(row: Mapping[str, Any]) -> str:
    primary = str(row.get("primary_ext") or "").lower()
    types = {str(value).lower() for value in row.get("types") or []}
    if primary == "person" or "person" in types:
        return "person"
    if primary in {"org", "organization"} or "organization" in types:
        return "organization"
    return "source_entity"


def _entity_row(
    *,
    kind: str,
    source_system_id: str,
    source_entity_id: str,
    display_name: str | None,
    attributes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    support = {
        "source_system_id": source_system_id,
        "source_entity_id": source_entity_id,
        "source_support_digest": _digest(
            {
                "attributes": attributes or {},
                "display_name": display_name,
                "source_entity_id": source_entity_id,
                "source_system_id": source_system_id,
            }
        ),
    }
    return {
        "schema_id": ENTITY_SCHEMA_ID,
        "canonical_entity_id": _canonical_id(
            kind, source_system_id, source_entity_id
        ),
        "entity_kind": kind,
        "display_name": display_name,
        "canonicalization_basis": "SOURCE_NATIVE_SINGLETON",
        "source_memberships": [support],
        "attributes": dict(attributes or {}),
    }


def _fact_row(
    *,
    subject_entity_id: str,
    predicate: str,
    source_system_id: str,
    source_assertion_id: str,
    object_entity_id: str | None = None,
    literal: Any = None,
    qualifiers: Mapping[str, Any] | None = None,
    layer: str,
    directed: bool,
    raw_valid_start: str | None = None,
    raw_valid_end: str | None = None,
    precision_bucket: str = "undated-only",
    observed_at: str | None = None,
    temporal_weight: float = 0.0,
) -> dict[str, Any]:
    if (object_entity_id is None) == (literal is None):
        raise CanonicalFactsError("canonical fact needs exactly one object form")
    support_payload = {
        "source_assertion_id": source_assertion_id,
        "source_system_id": source_system_id,
    }
    return {
        "schema_id": FACT_SCHEMA_ID,
        "fact_id": _fact_id(
            predicate,
            subject_entity_id,
            object_entity_id,
            literal,
            source_system_id,
            source_assertion_id,
        ),
        "subject_entity_id": subject_entity_id,
        "predicate": predicate,
        "object_entity_id": object_entity_id,
        "literal": literal,
        "qualifiers": dict(qualifiers or {}),
        "layer": layer,
        "directed": directed,
        "raw_valid_start": raw_valid_start,
        "raw_valid_end": raw_valid_end,
        "precision_bucket": precision_bucket,
        "observed_at": observed_at,
        "structural_weight": 1.0,
        "temporal_weight": float(temporal_weight),
        "fact_status": "ACTIVE",
        "support": {
            **support_payload,
            "source_support_digest": _digest(support_payload),
            "support_role": "DIRECT_SOURCE_ASSERTION",
        },
    }


def _file_entry(path: Path, *, role: str, schema_id: str, records: int) -> dict[str, Any]:
    return {
        "role": role,
        "path": path.name,
        "schema_id": schema_id,
        "records": records,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "encoding": "canonical-ndjson-gzip-mtime-0",
        "access_class": "source-governed-analysis-ready",
    }


def build_canonical_generation(
    *,
    p2_root: str | Path,
    lda_root: str | Path,
    politico_root: str | Path,
    identity_candidates_root: str | Path,
    legislators_current_path: str | Path,
    analysis_authority_manifest_path: str | Path,
    output_root: str | Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the real singleton canonical fact plane from passed corpora."""

    p2 = Path(p2_root).resolve()
    lda = Path(lda_root).resolve()
    politico = Path(politico_root).resolve()
    candidates_root = Path(identity_candidates_root).resolve()
    legislators_path = Path(legislators_current_path).resolve()
    authority_path = Path(analysis_authority_manifest_path).resolve()
    output = Path(output_root).resolve()
    required = (
        p2 / "source_native_corpus_receipt.json",
        lda / "lda_source_native_corpus_receipt.json",
        politico / "normalization_receipt.json",
        candidates_root / "lda_identity_candidate_receipt.json",
        legislators_path,
        authority_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise CanonicalFactsError(f"missing canonical inputs: {missing}")
    if output.exists():
        raise CanonicalFactsError(f"immutable canonical generation exists: {output}")

    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority_schema = json.loads(
        (Path(__file__).parent / "schemas" / "internal_analysis_authority.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validate_instance(authority, authority_schema)
    source_authority = authority["sources"]
    if not all(
        source["enabled_internal_analysis"] for source in source_authority.values()
    ):
        raise CanonicalFactsError(
            "all canonical inputs require explicit internal-analysis authority"
        )

    generated_at = generated_at or _now()
    legislators, legislator_names = _legislator_index(legislators_path)
    legislator_ids = {
        str(row["id"]["bioguide"]): _canonical_id(
            "legislator", "congress-legislators", f"bioguide:{row['id']['bioguide']}"
        )
        for row in legislators
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    entities_path = staging / "canonical_entities.ndjson.gz"
    facts_path = staging / "canonical_facts.ndjson.gz"
    links_path = staging / "canonical_identity_links.ndjson.gz"
    entity_counts: Counter[str] = Counter()
    entity_source_counts: Counter[str] = Counter()
    fact_counts: Counter[str] = Counter()
    fact_source_counts: Counter[str] = Counter()
    excluded_self_loop_counts: Counter[str] = Counter()
    contact_redaction_counts: Counter[str] = Counter()
    contact_only_facts_excluded = 0
    contact_only_fact_exclusion_counts: Counter[str] = Counter()
    empty_literal_facts_excluded = 0
    empty_literal_fact_exclusion_counts: Counter[str] = Counter()
    member_link_counts: Counter[str] = Counter()
    littlesis_kinds: dict[str, str] = {}
    known_source_entities: dict[tuple[str, str], str] = {}
    identity_target_sources = {
        "littlesis-bulk",
        "quorum-legacy-derived",
        "politico-pro-staffer-directory",
    }

    try:
        with _gzip_writer(entities_path) as entities, _gzip_writer(facts_path) as facts:
            def emit_entity(row: dict[str, Any]) -> str:
                sanitized = dict(row)
                sanitized["display_name"], display_redactions = _sanitize_free_text(
                    row.get("display_name")
                )
                sanitized["attributes"], attribute_redactions = _sanitize_free_text(
                    row.get("attributes") or {}
                )
                source = row["source_memberships"][0]["source_system_id"]
                redactions = display_redactions + attribute_redactions
                if redactions:
                    contact_redaction_counts[f"entity:{source}"] += redactions
                _write_row(entities, sanitized)
                entity_counts[row["entity_kind"]] += 1
                source_id = row["source_memberships"][0]["source_entity_id"]
                entity_source_counts[source] += 1
                # Only identity-candidate targets need reverse lookup.  Keeping
                # the million-plus LDA/activity keys here adds several GiB of
                # transient heap without improving validation or output.
                if source in identity_target_sources:
                    known_source_entities[(source, source_id)] = row[
                        "canonical_entity_id"
                    ]
                return str(row["canonical_entity_id"])

            def emit_fact(row: dict[str, Any]) -> None:
                nonlocal contact_only_facts_excluded, empty_literal_facts_excluded
                if row.get("object_entity_id") == row["subject_entity_id"]:
                    excluded_self_loop_counts[
                        f"{row['support']['source_system_id']}:{row['predicate']}"
                    ] += 1
                    return
                sanitized = dict(row)
                sanitized["literal"], literal_redactions = _sanitize_free_text(
                    row.get("literal")
                )
                sanitized["qualifiers"], qualifier_redactions = _sanitize_free_text(
                    row.get("qualifiers") or {}
                )
                redactions = literal_redactions + qualifier_redactions
                source = row["support"]["source_system_id"]
                if redactions:
                    contact_redaction_counts[f"fact:{source}"] += redactions
                if (
                    sanitized.get("object_entity_id") is None
                    and sanitized.get("literal") is None
                ):
                    key = f"{source}:{row['predicate']}"
                    if redactions:
                        contact_only_facts_excluded += 1
                        contact_only_fact_exclusion_counts[key] += 1
                    else:
                        empty_literal_facts_excluded += 1
                        empty_literal_fact_exclusion_counts[key] += 1
                    return
                if (
                    sanitized.get("precision_bucket") == "undated-only"
                    and float(sanitized.get("temporal_weight") or 0.0) != 0.0
                ):
                    raise CanonicalFactsError(
                        "undated-only canonical fact has nonzero temporal weight: "
                        f"{sanitized['fact_id']}"
                    )
                if sanitized.get("literal") != row.get("literal"):
                    sanitized["fact_id"] = _fact_id(
                        str(sanitized["predicate"]),
                        str(sanitized["subject_entity_id"]),
                        sanitized.get("object_entity_id"),
                        sanitized.get("literal"),
                        str(sanitized["support"]["source_system_id"]),
                        str(sanitized["support"]["source_assertion_id"]),
                    )
                _write_row(facts, sanitized)
                fact_counts[row["predicate"]] += 1
                fact_source_counts[source] += 1

            for person in sorted(
                legislators, key=lambda row: str(row["id"]["bioguide"])
            ):
                bioguide = str(person["id"]["bioguide"])
                names = person["name"]
                term = _latest_term(person)
                entity_id = emit_entity(
                    _entity_row(
                        kind="legislator",
                        source_system_id="congress-legislators",
                        source_entity_id=f"bioguide:{bioguide}",
                        display_name=str(
                            names.get("official_full")
                            or " ".join(
                                filter(None, [names.get("first"), names.get("last")])
                            )
                        ),
                        attributes={"bioguide_id": bioguide, "current_term": term or {}},
                    )
                )
                if term:
                    emit_fact(
                        _fact_row(
                            subject_entity_id=entity_id,
                            predicate="HAS_LEGISLATIVE_TERM",
                            literal=dict(term),
                            source_system_id="congress-legislators",
                            source_assertion_id=f"bioguide:{bioguide}:term:{term.get('start')}",
                            layer="legislator",
                            directed=True,
                            raw_valid_start=str(term.get("start") or "") or None,
                            raw_valid_end=str(term.get("end") or "") or None,
                            precision_bucket="day",
                            temporal_weight=1.0,
                        )
                    )

            for row in _read_rows(p2 / "littlesis_entities.ndjson.gz"):
                source_id = str(row["source_entity_id"])
                kind = _little_kind(row)
                littlesis_kinds[source_id] = kind
                emit_entity(
                    _entity_row(
                        kind=kind,
                        source_system_id="littlesis-bulk",
                        source_entity_id=source_id,
                        display_name=row.get("name"),
                        attributes={
                            "aliases": row.get("aliases") or [],
                            "primary_ext": row.get("primary_ext"),
                            "types": row.get("types") or [],
                        },
                    )
                )

            category_predicate = {
                "Position": ("POSITION_AT", "position"),
                "Education": ("EDUCATED_AT", "education"),
                "Lobbying": ("LOBBIED_FOR", "lobbying"),
            }
            for row in _read_rows(p2 / "littlesis_relationship_assertions.ndjson.gz"):
                predicate, layer = category_predicate[str(row["category_name"])]
                subject_source = str(row["subject_source_entity_id"])
                object_source = str(row["object_source_entity_id"])
                subject_kind = littlesis_kinds[subject_source]
                object_kind = littlesis_kinds[object_source]
                emit_fact(
                    _fact_row(
                        subject_entity_id=_canonical_id(
                            subject_kind, "littlesis-bulk", subject_source
                        ),
                        predicate=predicate,
                        object_entity_id=_canonical_id(
                            object_kind, "littlesis-bulk", object_source
                        ),
                        source_system_id="littlesis-bulk",
                        source_assertion_id=str(row["source_relationship_id"]),
                        qualifiers={
                            "category_id": row["category_id"],
                            "description1": row.get("description1"),
                            "description2": row.get("description2"),
                            "source_is_current": row.get("source_is_current", False),
                        },
                        layer=layer,
                        directed=True,
                        raw_valid_start=row.get("raw_start_date"),
                        raw_valid_end=row.get("raw_end_date"),
                        precision_bucket=str(row["precision_bucket"]),
                        observed_at=row.get("source_updated_at"),
                        temporal_weight=float(row.get("temporal_weight") or 0.0),
                    )
                )

            legacy_rows = list(
                _read_rows(p2 / "quorum_legacy_source_native_seed.ndjson")
            )
            office_keys = sorted(
                {str(row["member_office_source_key"]) for row in legacy_rows}
            )
            office_targets: dict[str, str] = {}
            for office_key in office_keys:
                candidates = legislator_names.get(_normalize_name(office_key), set())
                if len(candidates) == 1:
                    office_targets[office_key] = legislator_ids[next(iter(candidates))]
                    member_link_counts["legacy_offices_matched"] += 1
                else:
                    office_targets[office_key] = emit_entity(
                        _entity_row(
                            kind="legislative_office",
                            source_system_id="quorum-legacy-derived",
                            source_entity_id=f"office:{office_key}",
                            display_name=office_key,
                        )
                    )
                    member_link_counts[
                        "legacy_offices_ambiguous" if candidates else "legacy_offices_unmatched"
                    ] += 1
            for row in legacy_rows:
                source_id = str(row["source_entity_id"])
                person_id = emit_entity(
                    _entity_row(
                        kind="person",
                        source_system_id="quorum-legacy-derived",
                        source_entity_id=source_id,
                        display_name=row.get("display_name"),
                        attributes={
                            "featured": bool(row.get("featured")),
                            "title": row.get("title"),
                        },
                    )
                )
                office_key = str(row["member_office_source_key"])
                emit_fact(
                    _fact_row(
                        subject_entity_id=person_id,
                        predicate="STAFFS_FOR",
                        object_entity_id=office_targets[office_key],
                        source_system_id="quorum-legacy-derived",
                        source_assertion_id=f"legacy-staff:{source_id}",
                        qualifiers={"office_key": office_key, "title": row.get("title")},
                        layer="employment",
                        directed=True,
                    )
                )
                member_link_counts["legacy_staff_links"] += 1

            for row in _read_rows(politico / "persons.ndjson"):
                emit_entity(
                    _entity_row(
                        kind="person",
                        source_system_id="politico-pro-staffer-directory",
                        source_entity_id=str(row["source_person_id"]),
                        display_name=row.get("display_name"),
                        attributes={
                            "name_normalized": row.get("name_normalized"),
                            "party_partition": row.get("party_partition"),
                        },
                    )
                )
            for row in _read_rows(politico / "role_assertions.ndjson"):
                person_id = _canonical_id(
                    "person",
                    "politico-pro-staffer-directory",
                    str(row["source_person_id"]),
                )
                emit_fact(
                    _fact_row(
                        subject_entity_id=person_id,
                        predicate="HAS_DIRECTORY_ROLE_TEXT",
                        literal=str(row["role_text"]),
                        source_system_id="politico-pro-staffer-directory",
                        source_assertion_id=str(row["assertion_id"]),
                        layer="profile",
                        directed=True,
                        observed_at=row.get("observed_at"),
                    )
                )
                bioguide = _role_member_bioguide(
                    str(row["role_text"]), legislator_names
                )
                if bioguide is not None:
                    emit_fact(
                        _fact_row(
                            subject_entity_id=person_id,
                            predicate="STAFFS_FOR",
                            object_entity_id=legislator_ids[bioguide],
                            source_system_id="politico-pro-staffer-directory",
                            source_assertion_id=f"{row['assertion_id']}:member-link",
                            qualifiers={"role_text": row["role_text"]},
                            layer="employment",
                            directed=True,
                            observed_at=row.get("observed_at"),
                            temporal_weight=0.0,
                        )
                    )
                    member_link_counts["politico_member_role_links"] += 1
                else:
                    member_link_counts["politico_nonmember_or_unmatched_roles"] += 1
            for row in _read_rows(politico / "issue_assertions.ndjson"):
                emit_fact(
                    _fact_row(
                        subject_entity_id=_canonical_id(
                            "person",
                            "politico-pro-staffer-directory",
                            str(row["source_person_id"]),
                        ),
                        predicate="HAS_DIRECTORY_ISSUE_TEXT",
                        literal=str(row["issue_area"]),
                        source_system_id="politico-pro-staffer-directory",
                        source_assertion_id=str(row["assertion_id"]),
                        layer="profile",
                        directed=True,
                        observed_at=row.get("observed_at"),
                    )
                )

            for row in _read_rows(lda / "lda_lobbyist_assertions.ndjson.gz"):
                emit_entity(
                    _entity_row(
                        kind="person",
                        source_system_id="senate-lda-api",
                        source_entity_id=str(row["source_person_id"]),
                        display_name=row.get("display_name"),
                        attributes={
                            "filing_years": row.get("filing_years") or [],
                            "activity_support_count": row.get("activity_support_count", 0),
                            "covered_position_support_count": row.get(
                                "covered_position_support_count", 0
                            ),
                        },
                    )
                )
            for row in _read_rows(lda / "lda_organization_assertions.ndjson.gz"):
                emit_entity(
                    _entity_row(
                        kind="organization",
                        source_system_id="senate-lda-api",
                        source_entity_id=str(row["source_org_id"]),
                        display_name=row.get("display_name"),
                        attributes={"organization_kind": row["organization_kind"]},
                    )
                )
            for row in _read_rows(lda / "lda_activity_assertions.ndjson.gz"):
                emit_entity(
                    _entity_row(
                        kind="lda_activity",
                        source_system_id="senate-lda-api",
                        source_entity_id=str(row["source_activity_id"]),
                        display_name=row.get("general_issue_code_display"),
                        attributes={
                            "filing_uuid": row["filing_uuid"],
                            "filing_year": row["filing_year"],
                            "filing_period": row["filing_period"],
                            "general_issue_code": row["general_issue_code"],
                            "dt_posted": row["dt_posted"],
                        },
                    )
                )
            kind_map = {
                "person": "person",
                "lda_activity": "lda_activity",
                "registrant": "organization",
                "client": "organization",
            }
            for row in _read_rows(lda / "lda_industry_edges.ndjson.gz"):
                emit_fact(
                    _fact_row(
                        subject_entity_id=_canonical_id(
                            kind_map[str(row["subject_kind"])],
                            "senate-lda-api",
                            str(row["subject_id"]),
                        ),
                        predicate=str(row["predicate"]),
                        object_entity_id=_canonical_id(
                            kind_map[str(row["object_kind"])],
                            "senate-lda-api",
                            str(row["object_id"]),
                        ),
                        source_system_id="senate-lda-api",
                        source_assertion_id=str(row["edge_id"]),
                        qualifiers={"support_count": row["support_count"]},
                        layer="industry",
                        directed=True,
                        precision_bucket=str(row["precision_bucket"]),
                        temporal_weight=1.0,
                    )
                )
            for row in _read_rows(lda / "lda_covered_position_assertions.ndjson.gz"):
                emit_fact(
                    _fact_row(
                        subject_entity_id=_canonical_id(
                            "person", "senate-lda-api", str(row["source_person_id"])
                        ),
                        predicate="HAS_COVERED_POSITION_TEXT",
                        literal=str(row["covered_position"]),
                        source_system_id="senate-lda-api",
                        source_assertion_id=str(row["assertion_id"]),
                        qualifiers={
                            "source_activity_id": row["source_activity_id"],
                            "filing_uuid": row["filing_uuid"],
                            "government_term_hits": row.get("government_term_hits") or [],
                        },
                        layer="profile",
                        directed=True,
                        observed_at=row.get("dt_posted"),
                    )
                )

        link_counts: Counter[str] = Counter()
        with _gzip_writer(links_path) as links:
            target_source_map = {
                "littlesis": "littlesis-bulk",
                "politico-pro-staffer-directory": "politico-pro-staffer-directory",
                "quorum-legacy-staffers": "quorum-legacy-derived",
            }
            for row in _read_rows(
                candidates_root / "lda_identity_candidates.ndjson.gz"
            ):
                target_system = target_source_map[str(row["matched_source_system_id"])]
                target_source_id = str(row["matched_source_entity_id"])
                target_id = known_source_entities.get((target_system, target_source_id))
                if target_id is None:
                    raise CanonicalFactsError(
                        f"identity candidate target not materialized: {target_system}:{target_source_id}"
                    )
                unambiguous = (
                    row["lda_name_collision_max"] == 1
                    and row["target_cross_source_multiplicity_max"] == 1
                    and row["target_name_collision_max"] == 1
                    and all(
                        value <= 1
                        for value in row["target_multiplicity_by_source_max"].values()
                    )
                )
                link = {
                    "schema_id": IDENTITY_LINK_SCHEMA_ID,
                    "identity_link_id": str(row["candidate_id"]),
                    "left_entity_id": _canonical_id(
                        "person", "senate-lda-api", str(row["lda_source_person_id"])
                    ),
                    "right_entity_id": target_id,
                    "link_status": "UNAMBIGUOUS_REVIEW_CANDIDATE"
                    if unambiguous
                    else "REVIEW_REQUIRED",
                    "canonical_merge": False,
                    "method_version": row["method_version"],
                    "match_evidence": row["match_evidence"],
                    "source_binding_sha256": row["source_binding_sha256"],
                }
                _write_row(links, link)
                link_counts[link["link_status"]] += 1
                link_counts[f"target:{target_system}"] += 1

        files = [
            _file_entry(
                entities_path,
                role="canonical_entities",
                schema_id=ENTITY_SCHEMA_ID,
                records=sum(entity_counts.values()),
            ),
            _file_entry(
                facts_path,
                role="canonical_facts",
                schema_id=FACT_SCHEMA_ID,
                records=sum(fact_counts.values()),
            ),
            _file_entry(
                links_path,
                role="canonical_identity_links",
                schema_id=IDENTITY_LINK_SCHEMA_ID,
                records=link_counts["UNAMBIGUOUS_REVIEW_CANDIDATE"]
                + link_counts["REVIEW_REQUIRED"],
            ),
        ]
        receipt = {
            "schema_id": RECEIPT_SCHEMA_ID,
            "generated_at": generated_at,
            "status": "PASS",
            "canonicalization_version": "source-native-singleton-v6",
            "input_receipts": {
                "p2": sha256_file(p2 / "source_native_corpus_receipt.json"),
                "lda": sha256_file(lda / "lda_source_native_corpus_receipt.json"),
                "politico": sha256_file(politico / "normalization_receipt.json"),
                "identity_candidates": sha256_file(
                    candidates_root / "lda_identity_candidate_receipt.json"
                ),
                "legislators_current": sha256_file(legislators_path),
                "analysis_authority_manifest": sha256_file(authority_path),
            },
            "analysis_authority_manifest_sha256": sha256_file(authority_path),
            "source_activation": {
                source_id: (
                    "ENABLED_INTERNAL_ANALYSIS"
                    if source["enabled_internal_analysis"]
                    else "DISABLED_INTERNAL_ANALYSIS"
                )
                for source_id, source in sorted(source_authority.items())
            },
            "entity_counts": dict(sorted(entity_counts.items())),
            "entity_source_counts": dict(sorted(entity_source_counts.items())),
            "fact_counts": dict(sorted(fact_counts.items())),
            "fact_source_counts": dict(sorted(fact_source_counts.items())),
            "excluded_self_loop_facts": sum(excluded_self_loop_counts.values()),
            "excluded_self_loop_fact_counts": dict(
                sorted(excluded_self_loop_counts.items())
            ),
            "contact_redaction_counts": dict(
                sorted(contact_redaction_counts.items())
            ),
            "contact_only_facts_excluded": contact_only_facts_excluded,
            "contact_only_fact_exclusion_counts": dict(
                sorted(contact_only_fact_exclusion_counts.items())
            ),
            "empty_literal_facts_excluded": empty_literal_facts_excluded,
            "empty_literal_fact_exclusion_counts": dict(
                sorted(empty_literal_fact_exclusion_counts.items())
            ),
            "undated_nonzero_temporal_weights": 0,
            "member_link_counts": dict(sorted(member_link_counts.items())),
            "identity_link_counts": dict(sorted(link_counts.items())),
            "canonical_entity_merges": 0,
            "cross_source_memberships": 0,
            "identity_candidates_are_facts": False,
            "canonical_facts_materialized": True,
            "internal_analysis_enabled": all(
                source["enabled_internal_analysis"]
                for source in source_authority.values()
            ),
            "raw_redistribution_claimed": bool(
                authority["raw_redistribution_claimed"]
            ),
            "files": files,
            "failures": [],
        }
        (staging / "canonical_generation_receipt.json").write_bytes(
            canonical_json_bytes(receipt)
        )
        os.replace(staging, output)
        return receipt
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


__all__ = [
    "CanonicalFactsError",
    "build_canonical_generation",
    "ENTITY_SCHEMA_ID",
    "FACT_SCHEMA_ID",
    "IDENTITY_LINK_SCHEMA_ID",
    "RECEIPT_SCHEMA_ID",
]
