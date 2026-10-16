# -*- coding: utf-8 -*-
"""Build a deterministic, staging-only legislation membership triage generation.

The assistant never deletes discovery candidates, never edits the review ledger
or user decision file, and never treats article absence as evidence.  Machine
proposals are structurally non-scoring: every proposal retains
``disposition="unresolved"`` and records the suggestion only in
``proposed_disposition`` with ``adjudication_state="machine_proposed"``.

The only automatic proposals are:

* include an exact Congress/type/number known-outline membership;
* exclude an exact official-title collision with a ratified exclusion for the
  same category; or
* exclude an explicitly tagged, localized official operative provision that
  affirmatively identifies an unrelated domain and carries complete evidence.

All other cases, including generic ``Act`` matches, short aliases, named-Act
references without localized action, missing text, broad industry relevance,
cross-category measures, and article-only evidence remain unresolved.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "legislation-membership-triage-v1"
MACHINE_REVIEWER = "deterministic legislation membership triage assistant"
MACHINE_STATE = "machine_proposed"
# Allowlist; same set the culling contract uses (legislation_culling_integration_contract.py).
RATIFIED_STATES = {"human_ratified", "reverified_preexisting_project_decision"}
GENERIC_TOKENS = {
    "a",
    "an",
    "the",
    "this",
    "that",
    "of",
    "and",
    "or",
    "for",
    "to",
    "in",
    "on",
    "act",
    "bill",
    "law",
    "resolution",
    "legislation",
}
INDUSTRY_RE = re.compile(
    r"\b(?:credit unions?|federal credit union|state[- ]chartered credit union|"
    r"national credit union administration|NCUA|share insurance fund|"
    r"depository institutions?|financial institutions?|consumer financial|"
    r"interchange fees?|credit cards?|payment systems?|FedNow|mortgage|"
    r"member business lending|field of membership|CDFI|community development "
    r"financial institution|banking services?|deposit accounts?)\b",
    re.I,
)
CANONICAL_RE = re.compile(
    r"^(?P<congress>\d+):"
    r"(?P<measure_type>hconres|sconres|hjres|sjres|hamdt|samdt|hres|sres|hr|s):"
    r"(?P<number>\d+)$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

BUCKETS = (
    "ratified_existing_decision",
    "proposed_include_exact_known_outline",
    "proposed_exclude_exact_title_collision",
    "proposed_exclude_affirmative_unrelated_official_provision",
    "unresolved_broad_industry_relevance_retained",
    "unresolved_cross_category_measure_retained",
    "unresolved_short_alias_non_dispositive",
    "unresolved_named_act_reference_non_dispositive",
    "unresolved_official_text_unavailable",
    "unresolved_article_only_non_dispositive",
    "unresolved_weak_or_ambiguous",
)


class TriageError(RuntimeError):
    """A fail-closed input or staging contract violation."""


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalized_title(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", normalize(value).casefold()))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: Any) -> str:
    return sha256_bytes(normalize(value).encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    return sha256_bytes(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    )


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TriageError(f"cannot read JSON {path}: {exc}") from exc


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise TriageError(f"required NDJSON file is absent: {path}")
    rows = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TriageError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise TriageError(f"non-object row at {path}:{line_number}")
            rows.append(dict(row, _input_line=line_number))
    return rows


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def canonical_key(value: Any) -> str:
    key = normalize(value).casefold()
    match = CANONICAL_RE.fullmatch(key)
    if not match:
        raise TriageError(f"invalid type-aware canonical key: {value!r}")
    return (
        f"{int(match.group('congress'))}:{match.group('measure_type')}:"
        f"{int(match.group('number'))}"
    )


def compact_cite_key(congress: Any, compact_cite: Any) -> str | None:
    match = re.fullmatch(
        r"(hconres|sconres|hjres|sjres|hamdt|samdt|hres|sres|hr|s)(\d+)",
        re.sub(r"[^a-z0-9]", "", normalize(compact_cite).casefold()),
    )
    if not match:
        return None
    return f"{int(congress)}:{match.group(1)}:{int(match.group(2))}"


def complete_manifest(path: Path, expected_schema: str | None = None) -> dict[str, Any]:
    manifest = read_json(path)
    if expected_schema and manifest.get("schema") != expected_schema:
        raise TriageError(
            f"unexpected manifest schema at {path}: {manifest.get('schema')!r}"
        )
    if manifest.get("status") != "complete":
        raise TriageError(f"source manifest is not complete: {path}")
    return manifest


def resolve_manifest_file(
    directory: Path, manifest: dict[str, Any], *field_names: str
) -> Path:
    for field in field_names:
        value = manifest.get(field)
        if value:
            candidate = Path(str(value))
            path = candidate if candidate.is_absolute() else directory / candidate.name
            if path.is_file():
                return path
    raise TriageError(
        f"manifest in {directory} lacks an existing file for {field_names}"
    )


def is_short_act_alias(value: Any) -> bool:
    tokens = re.findall(r"[a-z0-9]+", normalize(value).casefold())
    distinctive = [token for token in tokens if token not in GENERIC_TOKENS]
    return "act" in tokens and len(set(distinctive)) < 3


def is_specific_full_title(value: Any) -> bool:
    tokens = re.findall(r"[a-z0-9]+", normalize(value).casefold())
    distinctive = [token for token in tokens if token not in GENERIC_TOKENS]
    return len(distinctive) >= 3 and len(tokens) >= 4


def is_ratified(row: dict[str, Any]) -> bool:
    disposition = row.get("disposition")
    state = normalize(row.get("adjudication_state")).casefold()
    return (
        disposition in {"include", "exclude_false_positive"}
        and state in RATIFIED_STATES
        and bool(normalize(row.get("reviewer")))
    )


def assert_staging_path(path: Path, workspace_root: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == workspace_root.resolve():
        raise TriageError("triage output cannot be the workspace root")
    if not any(re.search(r"(audit|stag|triage)", part, re.I) for part in resolved.parts):
        raise TriageError("output must explicitly be an audit/staging/triage directory")
    if resolved.exists():
        raise TriageError(f"immutable staging output already exists: {resolved}")
    return resolved


def contexts_from_text_row(row: dict[str, Any]) -> list[str]:
    contexts = []
    for item in row.get("identifier_evidence", []) or []:
        if isinstance(item, dict) and normalize(item.get("context")):
            contexts.append(normalize(item["context"]))
    return contexts


def official_title(
    candidate: dict[str, Any],
    metadata: dict[str, Any],
    record: dict[str, Any],
) -> str | None:
    for value in (
        metadata.get("official_title"),
        candidate.get("official_title"),
        candidate.get("title"),
        record.get("title"),
        record.get("purpose"),
    ):
        if normalize(value):
            return normalize(value)
    return None


def official_url(
    candidate: dict[str, Any],
    metadata: dict[str, Any],
    record: dict[str, Any],
) -> str | None:
    for value in (
        metadata.get("canonical_url"),
        metadata.get("source_url"),
        candidate.get("congress_gov_url"),
        candidate.get("official_text_source_url"),
        record.get("canonical_url"),
    ):
        url = normalize(value)
        if re.match(r"https://(?:www\.)?congress\.gov/", url, re.I):
            return url
    return None


def retrieval_timestamp(
    text_row: dict[str, Any] | None,
    relationship_manifest: dict[str, Any],
    text_manifest: dict[str, Any],
    run_manifest: dict[str, Any],
) -> str:
    for value in (
        (text_row or {}).get("collected_at"),
        relationship_manifest.get("completed_at"),
        text_manifest.get("completed_at"),
        run_manifest.get("completed_at"),
    ):
        if normalize(value):
            return normalize(value)
    return "source_manifest_timestamp_unavailable"


def evidence_record(
    *,
    source_url: str,
    excerpt: str,
    text_version: str,
    section: str,
    retrieved_at: str,
    basis: str,
    provenance: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_excerpt = normalize(excerpt)
    if not re.match(r"https://(?:www\.)?congress\.gov/", source_url, re.I):
        raise TriageError(f"proposal lacks an official Congress.gov URL: {source_url!r}")
    if not all(
        normalize(value)
        for value in (normalized_excerpt, text_version, section, retrieved_at, basis)
    ):
        raise TriageError("proposal evidence is incomplete")
    return {
        "evidence_basis": basis,
        "official_source_url": source_url,
        "official_text_version": normalize(text_version),
        "official_section": normalize(section),
        "official_evidence_excerpt": normalized_excerpt,
        "official_evidence_sha256": sha256_text(normalized_excerpt),
        "official_retrieved_at": normalize(retrieved_at),
        "evidence_sources_checked": ["official_congress_gov"],
        "proposal_provenance": provenance,
    }


def load_inputs(
    *,
    run_dir: Path,
    registry_path: Path,
    relationships_dir: Path,
    text_dir: Path,
    article_dir: Path,
    review_ledger_path: Path,
) -> dict[str, Any]:
    run_manifest_path = run_dir / "manifest.json"
    run_manifest = complete_manifest(run_manifest_path)
    union = run_manifest.get("union") or {}
    records_path = run_dir / Path(str(union.get("records_file") or "")).name
    memberships_path = run_dir / Path(
        str(union.get("memberships_file") or "")
    ).name
    records = read_ndjson(records_path)
    memberships = read_ndjson(memberships_path)

    registry = read_json(registry_path)
    if not isinstance(registry.get("categories"), list):
        raise TriageError("registry lacks categories")

    relationship_manifest_path = relationships_dir / "manifest.json"
    relationship_manifest = complete_manifest(relationship_manifest_path)
    metadata_path = resolve_manifest_file(
        relationships_dir,
        relationship_manifest,
        "measure_metadata_file",
        "metadata_file",
    )
    metadata = read_ndjson(metadata_path)

    text_manifest_path = text_dir / "manifest.json"
    text_manifest = complete_manifest(
        text_manifest_path,
        "congress-text-relevance-enrichment-manifest-v2",
    )
    text_ledger_path = resolve_manifest_file(
        text_dir, text_manifest, "ledger_file"
    )
    text_rows = read_ndjson(text_ledger_path)

    article_manifest_path = article_dir / "manifest.json"
    article_manifest = complete_manifest(article_manifest_path)
    article_membership_path = resolve_manifest_file(
        article_dir,
        article_manifest,
        "membership_evidence_file",
        "membership_ledger_file",
    )
    article_rows = read_ndjson(article_membership_path)

    review_rows = read_ndjson(review_ledger_path)
    paths = {
        "run_manifest": run_manifest_path,
        "records": records_path,
        "memberships": memberships_path,
        "registry": registry_path,
        "relationship_manifest": relationship_manifest_path,
        "relationship_metadata": metadata_path,
        "text_manifest": text_manifest_path,
        "text_ledger": text_ledger_path,
        "article_manifest": article_manifest_path,
        "article_membership": article_membership_path,
        "review_ledger": review_ledger_path,
    }
    return {
        "run_manifest": run_manifest,
        "records": records,
        "memberships": memberships,
        "registry": registry,
        "relationship_manifest": relationship_manifest,
        "metadata": metadata,
        "text_manifest": text_manifest,
        "text_rows": text_rows,
        "article_manifest": article_manifest,
        "article_rows": article_rows,
        "review_rows": review_rows,
        "paths": paths,
    }


def build_candidates(inputs: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    registry = inputs["registry"]
    categories = {row["id"]: row for row in registry["categories"]}
    record_by_key = {
        canonical_key(row["canonical_key"]): row for row in inputs["records"]
    }
    review_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in inputs["review_rows"]:
        identity = (canonical_key(row["canonical_key"]), normalize(row["query_id"]))
        if identity in review_by_key:
            raise TriageError(f"duplicate review membership: {identity}")
        review_by_key[identity] = row

    candidates = {identity: dict(row) for identity, row in review_by_key.items()}
    raw_category_edges = set()
    for membership in inputs["memberships"]:
        key = canonical_key(membership["canonical_key"])
        query_id = normalize(membership.get("query_id"))
        parent = normalize(membership.get("parent_category_id"))
        if query_id not in categories and parent in categories:
            query_id = parent
        if query_id not in categories:
            continue
        identity = (key, query_id)
        raw_category_edges.add(identity)
        if identity in candidates:
            candidates[identity].setdefault("raw_membership_present", True)
            continue
        record = record_by_key.get(key)
        if record is None:
            raise TriageError(f"membership references an absent discovery record: {key}")
        category = categories[query_id]
        candidates[identity] = {
            "canonical_key": key,
            "query_id": query_id,
            "category_label": category.get("label"),
            "scoring_category": bool(category.get("scoring_category")),
            "display_number": record.get("display_number"),
            "title": record.get("title") or record.get("purpose"),
            "record_kind": membership.get("record_kind"),
            "membership_non_dispositive": bool(
                membership.get("membership_non_dispositive")
            ),
            "disposition": "unresolved",
            "candidate_retained_in_discovery_corpus": True,
            "raw_membership_present": True,
            "review_ledger_row_absent": True,
        }
    if not raw_category_edges.issubset(set(candidates)):
        raise TriageError("candidate union lost one or more raw category memberships")
    rows = [
        dict(row, canonical_key=key, query_id=query_id)
        for (key, query_id), row in candidates.items()
    ]
    rows.sort(key=lambda row: (row["query_id"], row["canonical_key"]))
    return rows, {
        "categories": categories,
        "record_by_key": record_by_key,
        "raw_category_edges": raw_category_edges,
    }


def known_outline_edges(registry: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result = {}
    for category in registry["categories"]:
        for bill in category.get("known_bills", []) or []:
            key = compact_cite_key(bill.get("congress"), bill.get("compact_cite"))
            if key:
                result[(key, category["id"])] = bill
    return result


def ratified_title_templates(
    candidates: list[dict[str, Any]],
    metadata_by_key: dict[str, dict[str, Any]],
    record_by_key: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    templates: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in candidates:
        if not (
            is_ratified(row)
            and row.get("disposition") == "exclude_false_positive"
        ):
            continue
        key = row["canonical_key"]
        title = official_title(
            row, metadata_by_key.get(key, {}), record_by_key.get(key, {})
        )
        normalized = normalized_title(title)
        if normalized and is_specific_full_title(title):
            templates[(row["query_id"], normalized)].append(row)
    return templates


def explicit_unrelated_evidence(
    candidate: dict[str, Any], text_row: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Return only explicitly tagged, complete operative unrelated-domain evidence."""
    items = []
    if text_row:
        items.extend(text_row.get("identifier_evidence", []) or [])
        items.extend(text_row.get("affirmative_unrelated_domain_evidence", []) or [])
    items.extend(candidate.get("affirmative_unrelated_domain_evidence", []) or [])
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("affirmative_unrelated_domain") is not True:
            continue
        context = normalize(
            item.get("official_evidence_excerpt") or item.get("context")
        )
        domain = normalize(item.get("unrelated_domain"))
        section = normalize(item.get("official_section") or item.get("section"))
        if not (context and domain and section):
            continue
        localized = item.get("localized_match") is True
        operative = (
            item.get("localized_legislative_action_found") is True
            or normalize(item.get("evidence_kind"))
            == "localized_official_operative_provision"
        )
        if not (localized and operative):
            continue
        if INDUSTRY_RE.search(context):
            # Broad financial or credit-union reach defeats automated exclusion.
            continue
        expected_hash = normalize(
            item.get("official_evidence_sha256") or item.get("context_sha256")
        ).casefold()
        if expected_hash and (
            not SHA256_RE.fullmatch(expected_hash)
            or expected_hash != sha256_text(context)
        ):
            continue
        return {
            "context": context,
            "unrelated_domain": domain,
            "section": section,
            "reason": normalize(item.get("unrelated_reason"))
            or (
                "Localized official operative language affirmatively regulates "
                f"the unrelated domain {domain}."
            ),
        }
    return None


def proposal_base(
    *,
    candidate: dict[str, Any],
    proposed_disposition: str,
    reason_code: str,
    reason: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "record_type": "membership_decision_proposal",
        "canonical_key": candidate["canonical_key"],
        "query_id": candidate["query_id"],
        # Intentionally unresolved: this file cannot be used as a scoring ledger.
        "disposition": "unresolved",
        "proposed_disposition": proposed_disposition,
        "decision_type": "membership_category_relevance_proposal",
        "reason_code": reason_code,
        "reason": reason,
        **evidence,
        "reviewer_confidence": "high",
        "adjudication_state": MACHINE_STATE,
        "reviewer": MACHINE_REVIEWER,
        "reviewed_at": None,
        "requires_human_ratification": True,
        "scoring_eligible": False,
        "candidate_retained_in_discovery_corpus": True,
    }


def triage(inputs: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates, indexes = build_candidates(inputs)
    record_by_key = indexes["record_by_key"]
    metadata_by_key = {
        canonical_key(row["canonical_key"]): row for row in inputs["metadata"]
    }
    text_by_membership = {
        (canonical_key(row["canonical_key"]), normalize(row["query_id"])): row
        for row in inputs["text_rows"]
        if row.get("evidence_role") in {None, "category_membership"}
    }
    article_by_membership = {
        (canonical_key(row["canonical_key"]), normalize(row["query_id"])): row
        for row in inputs["article_rows"]
    }
    known = known_outline_edges(inputs["registry"])
    templates = ratified_title_templates(
        candidates, metadata_by_key, record_by_key
    )
    categories_per_measure: dict[str, set[str]] = collections.defaultdict(set)
    for row in candidates:
        categories_per_measure[row["canonical_key"]].add(row["query_id"])

    proposals = []
    output = []
    source_hashes = {
        name: sha256_file(path) for name, path in inputs["paths"].items()
    }
    for candidate in candidates:
        key = candidate["canonical_key"]
        query_id = candidate["query_id"]
        identity = (key, query_id)
        source_review_disposition = candidate.get("disposition")
        source_review_adjudication_state = candidate.get("adjudication_state")
        metadata = metadata_by_key.get(key, {})
        record = record_by_key.get(key, {})
        text_row = text_by_membership.get(identity)
        article_row = article_by_membership.get(identity)
        title = official_title(candidate, metadata, record)
        url = official_url(candidate, metadata, record)
        contexts = contexts_from_text_row(text_row or {})
        combined_official = " ".join(
            filter(
                None,
                (
                    title,
                    normalize(metadata.get("summary")),
                    *contexts,
                ),
            )
        )
        cross_category_count = len(categories_per_measure[key])
        article_count = int(
            (article_row or {}).get("trusted_article_match_count")
            or len((article_row or {}).get("trusted_article_evidence", []) or [])
            or 0
        )
        row = {
            **candidate,
            "schema": SCHEMA,
            "canonical_key": key,
            "query_id": query_id,
            "official_title": title,
            "official_source_url": url,
            "official_text_evidence_state": (
                (text_row or {}).get("evidence_state")
            ),
            "official_text_version": (text_row or {}).get("version_label"),
            "official_text_contexts": contexts,
            "trusted_article_match_count": article_count,
            "article_absence_is_never_exclusion_evidence": True,
            "article_evidence_is_corroborative_not_dispositive": True,
            "generic_act_language_is_non_dispositive": True,
            "short_alias_is_non_dispositive": True,
            "cross_category_membership_count": cross_category_count,
            "candidate_retained_in_discovery_corpus": True,
            "machine_proposal": None,
            "scoring_eligible_from_triage": False,
            "source_review_disposition": source_review_disposition,
            "source_review_adjudication_state": source_review_adjudication_state,
        }

        if is_ratified(candidate):
            bucket = "ratified_existing_decision"
            status = "ratified_existing"
            # The source decision remains byte-for-byte represented in the
            # source row; this assistant does not rewrite or supersede it.
        elif identity in known:
            bill = known[identity]
            if not (url and title):
                bucket = "unresolved_official_text_unavailable"
                status = "unresolved"
            else:
                evidence = evidence_record(
                    source_url=url,
                    excerpt=title,
                    text_version="Congress.gov official measure title",
                    section="Official title",
                    retrieved_at=retrieval_timestamp(
                        text_row,
                        inputs["relationship_manifest"],
                        inputs["text_manifest"],
                        inputs["run_manifest"],
                    ),
                    basis="explicit_named_outline_bill",
                    provenance=[
                        {
                            "source": "registry_known_bill",
                            "registry_sha256": source_hashes["registry"],
                            "source_paragraph": bill.get("source_paragraph"),
                            "source_text": bill.get("source_text"),
                            "compact_cite": bill.get("compact_cite"),
                        },
                        {
                            "source": "exact_congress_qualified_discovery_identity",
                            "records_sha256": source_hashes["records"],
                            "canonical_key": key,
                        },
                    ],
                )
                proposal = proposal_base(
                    candidate=candidate,
                    proposed_disposition="include",
                    reason_code="exact_congress_qualified_known_outline_bill",
                    reason=(
                        "The exact Congress/type/number membership is named in "
                        "the source outline and independently resolves to this "
                        "Congress.gov measure."
                    ),
                    evidence=evidence,
                )
                proposals.append(proposal)
                row["machine_proposal"] = proposal
                bucket = "proposed_include_exact_known_outline"
                status = MACHINE_STATE
        else:
            normalized = normalized_title(title)
            collision_templates = templates.get((query_id, normalized), [])
            unrelated = explicit_unrelated_evidence(candidate, text_row)
            if (
                collision_templates
                and title
                and url
                and is_specific_full_title(title)
            ):
                template = sorted(
                    collision_templates,
                    key=lambda item: item["canonical_key"],
                )[0]
                evidence = evidence_record(
                    source_url=url,
                    excerpt=title,
                    text_version="Congress.gov official measure title",
                    section="Official title",
                    retrieved_at=retrieval_timestamp(
                        text_row,
                        inputs["relationship_manifest"],
                        inputs["text_manifest"],
                        inputs["run_manifest"],
                    ),
                    basis="official_title_and_provisions",
                    provenance=[
                        {
                            "source": "exact_normalized_official_title_collision",
                            "normalized_official_title": normalized,
                            "ratified_template_canonical_key": template[
                                "canonical_key"
                            ],
                            "ratified_template_query_id": template["query_id"],
                            "ratified_template_row_sha256": stable_hash(
                                {
                                    key: value
                                    for key, value in template.items()
                                    if key != "_input_line"
                                }
                            ),
                            "review_ledger_sha256": source_hashes[
                                "review_ledger"
                            ],
                        }
                    ],
                )
                proposal = proposal_base(
                    candidate=candidate,
                    proposed_disposition="exclude_false_positive",
                    reason_code="exact_official_title_collision_with_ratified_exclusion",
                    reason=(
                        "The full normalized Congress.gov official title exactly "
                        "matches a ratified unrelated-domain exclusion for this "
                        "category. The candidate remains retained pending review."
                    ),
                    evidence=evidence,
                )
                proposals.append(proposal)
                row["machine_proposal"] = proposal
                bucket = "proposed_exclude_exact_title_collision"
                status = MACHINE_STATE
            elif unrelated and url:
                evidence = evidence_record(
                    source_url=url,
                    excerpt=unrelated["context"],
                    text_version=normalize(
                        (text_row or {}).get("version_label")
                    )
                    or "Congress.gov official text",
                    section=unrelated["section"],
                    retrieved_at=retrieval_timestamp(
                        text_row,
                        inputs["relationship_manifest"],
                        inputs["text_manifest"],
                        inputs["run_manifest"],
                    ),
                    basis="operative_provisions",
                    provenance=[
                        {
                            "source": "localized_official_operative_provision",
                            "text_ledger_sha256": source_hashes["text_ledger"],
                            "unrelated_domain": unrelated["unrelated_domain"],
                            "official_context_sha256": sha256_text(
                                unrelated["context"]
                            ),
                        }
                    ],
                )
                proposal = proposal_base(
                    candidate=candidate,
                    proposed_disposition="exclude_false_positive",
                    reason_code="affirmative_unrelated_official_operative_provision",
                    reason=unrelated["reason"],
                    evidence=evidence,
                )
                proposals.append(proposal)
                row["machine_proposal"] = proposal
                bucket = (
                    "proposed_exclude_affirmative_unrelated_official_provision"
                )
                status = MACHINE_STATE
            else:
                short_alias = bool(
                    candidate.get("ambiguous_short_act_alias_probe")
                    or candidate.get("matched_short_act_title_aliases")
                    or any(
                        is_short_act_alias(value)
                        for value in candidate.get("source_title_aliases", []) or []
                    )
                )
                act_reference_only = bool(
                    candidate.get(
                        "official_text_act_reference_only_evidence"
                    )
                    or candidate.get(
                        "matched_named_act_references_non_dispositive"
                    )
                    or any(
                        item.get("act_reference_only")
                        for item in (text_row or {}).get(
                            "identifier_evidence", []
                        )
                        if isinstance(item, dict)
                    )
                )
                text_state = (text_row or {}).get("evidence_state")
                if INDUSTRY_RE.search(combined_official):
                    bucket = "unresolved_broad_industry_relevance_retained"
                elif cross_category_count > 1:
                    bucket = "unresolved_cross_category_measure_retained"
                elif short_alias:
                    bucket = "unresolved_short_alias_non_dispositive"
                elif act_reference_only:
                    bucket = "unresolved_named_act_reference_non_dispositive"
                elif text_state in {None, "official_text_unavailable"}:
                    bucket = "unresolved_official_text_unavailable"
                elif article_count:
                    bucket = "unresolved_article_only_non_dispositive"
                else:
                    bucket = "unresolved_weak_or_ambiguous"
                status = "unresolved"
        row["triage_bucket"] = bucket
        row["triage_status"] = status
        row["requires_human_adjudication"] = status != "ratified_existing"
        if status == MACHINE_STATE:
            row["disposition"] = "unresolved"
            row["adjudication_state"] = MACHINE_STATE
        elif status == "unresolved":
            row["disposition"] = "unresolved"
            row["adjudication_state"] = "unresolved_pending_review"
        output.append(row)

    output.sort(key=lambda row: (row["triage_bucket"], row["query_id"], row["canonical_key"]))
    proposals.sort(
        key=lambda row: (
            row["proposed_disposition"],
            row["query_id"],
            row["canonical_key"],
        )
    )
    return output, proposals


def build(
    *,
    run_dir: Path,
    registry_path: Path,
    relationships_dir: Path,
    text_dir: Path,
    article_dir: Path,
    review_ledger_path: Path,
    out_dir: Path,
    workspace_root: Path = ROOT,
) -> dict[str, Any]:
    out_dir = assert_staging_path(out_dir, workspace_root)
    inputs = load_inputs(
        run_dir=run_dir.resolve(),
        registry_path=registry_path.resolve(),
        relationships_dir=relationships_dir.resolve(),
        text_dir=text_dir.resolve(),
        article_dir=article_dir.resolve(),
        review_ledger_path=review_ledger_path.resolve(),
    )
    ledger, proposals = triage(inputs)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(
        tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=str(out_dir.parent))
    )
    try:
        buckets_dir = temp / "buckets"
        buckets_dir.mkdir()
        bucket_rows = {
            bucket: [
                row for row in ledger if row["triage_bucket"] == bucket
            ]
            for bucket in BUCKETS
        }
        observed_buckets = {row["triage_bucket"] for row in ledger}
        if not observed_buckets.issubset(BUCKETS):
            raise TriageError(
                f"unregistered triage buckets: {sorted(observed_buckets - set(BUCKETS))}"
            )
        write_ndjson(temp / "triage_ledger.ndjson", ledger)
        write_ndjson(temp / "proposed_decisions.ndjson", proposals)
        pending = [
            row for row in ledger if row["triage_status"] != "ratified_existing"
        ]
        ratified = [
            row for row in ledger if row["triage_status"] == "ratified_existing"
        ]
        write_ndjson(temp / "unresolved_pending_human_adjudication.ndjson", pending)
        write_ndjson(temp / "ratified_existing_decisions.ndjson", ratified)
        for bucket in BUCKETS:
            write_ndjson(buckets_dir / f"{bucket}.ndjson", bucket_rows[bucket])

        input_receipts = {
            name: {
                "path": str(path.resolve()),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for name, path in sorted(inputs["paths"].items())
        }
        generation_id = sha256_bytes(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "inputs": {
                        name: receipt["sha256"]
                        for name, receipt in input_receipts.items()
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        output_files = [
            temp / "triage_ledger.ndjson",
            temp / "proposed_decisions.ndjson",
            temp / "unresolved_pending_human_adjudication.ndjson",
            temp / "ratified_existing_decisions.ndjson",
            *(buckets_dir / f"{bucket}.ndjson" for bucket in BUCKETS),
        ]
        proposed_counts = collections.Counter(
            row["proposed_disposition"] for row in proposals
        )
        bucket_counts = {
            bucket: len(bucket_rows[bucket]) for bucket in BUCKETS
        }
        manifest = {
            "schema": SCHEMA,
            "generation_id": generation_id,
            "deterministic": True,
            "generated_at": None,
            "staging_only": True,
            "production_or_user_decisions_modified": False,
            "candidate_deletion_count": 0,
            "article_absence_used_as_evidence_count": 0,
            "generic_act_used_as_evidence_count": 0,
            "machine_proposals_are_scoring_eligible": False,
            "machine_adjudication_state": MACHINE_STATE,
            "triage_membership_count": len(ledger),
            "distinct_membership_key_count": len(
                {(row["canonical_key"], row["query_id"]) for row in ledger}
            ),
            "proposal_count": len(proposals),
            "proposed_disposition_counts": dict(sorted(proposed_counts.items())),
            "ratified_existing_count": len(ratified),
            "pending_human_adjudication_count": len(pending),
            "bucket_counts": bucket_counts,
            "inputs": input_receipts,
            "outputs": {
                path.relative_to(temp).as_posix(): {
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in output_files
            },
            "acceptance_for_scoring": False,
            "reviewer_ratification_required": True,
        }
        write_json(temp / "manifest.json", manifest)
        for attempt in range(8):
            try:
                os.replace(temp, out_dir)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.05 * (2**attempt), 0.75))
        return manifest
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Build deterministic, non-scoring membership triage proposals for "
            "a complete merged Congress.gov run."
        )
    )
    result.add_argument("--run", required=True, type=Path)
    result.add_argument("--registry", required=True, type=Path)
    result.add_argument("--relationships", required=True, type=Path)
    result.add_argument("--text-relevance", required=True, type=Path)
    result.add_argument("--article-cross-reference", required=True, type=Path)
    result.add_argument("--review-ledger", required=True, type=Path)
    result.add_argument("--out", required=True, type=Path)
    result.add_argument("--workspace-root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = build(
            run_dir=args.run,
            registry_path=args.registry,
            relationships_dir=args.relationships,
            text_dir=args.text_relevance,
            article_dir=args.article_cross_reference,
            review_ledger_path=args.review_ledger,
            out_dir=args.out,
            workspace_root=args.workspace_root,
        )
    except TriageError as exc:
        print(f"build_legislation_membership_triage: FAIL CLOSED: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2, sort_keys=True))
    # A successful triage generation is still not scoring acceptance.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
