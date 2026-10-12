#!/usr/bin/env python3
"""Build the deterministic 19-category adjudication review queue.

``category_evidence`` is empty for all 18,557 candidates, so
``membership_is_finalizable`` returns False for all 64,004 memberships and the
reversible culling engine flags every one of them.  Nothing can leave the
flagged queue until a real 19-category review exists.  This program builds the
*apparatus* for that review: it emits one prioritised, hash-addressed queue row
per ``(canonical_key, query_id)`` still needing adjudication, carrying the
query terms that caused the membership, the official title, the official
Congress.gov links, and an explicit statement of what evidence is still
missing.

It is deliberately incapable of deciding anything:

* It never writes ``category_evidence``, a membership decision, a disposition,
  a junk exclusion, or a restoration.
* It never marks a row ``keep`` or ``junk``.  Every emitted row is
  ``unresolved``.
* Missing or incomplete evidence is recorded as a *blocker* on an unresolved
  row.  A blocker can never become an exclusion.
* It retires a row from the queue only when the project's own
  ``membership_is_finalizable`` contract accepts a supplied decision against
  the supplied evidence.  It has no private notion of "done".

Term matching against locally held discovery text is **navigational, not
evidentiary**.  Congress.gov searched full bill text and summaries that are
not held locally, so the absence of a local match means nothing and is
recorded as neutral.

Reads discovery records/memberships, the 62 discovery query files, candidate
evidence, and (optionally) existing membership decisions.  Writes only into
its own hash-addressed output root.  Performs no network request, no culling,
no scoring, no sync, no promotion, and no main-corpus write.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:
    from lane_file_io import iter_ndjson_raw_lines
    from legislation_measure_key import CANONICAL_KEY_RE
    from legislation.legislation_culling_integration_contract import (
        CullingContractError,
        membership_is_finalizable,
        validate_candidate_evidence,
    )
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import iter_ndjson_raw_lines
    from fec.legislation_measure_key import CANONICAL_KEY_RE
    from fec.legislation.legislation_culling_integration_contract import (
        CullingContractError,
        membership_is_finalizable,
        validate_candidate_evidence,
    )


TOOL_NAME = "build_legislation_category_review_queue.py"
TOOL_VERSION = "1.0.0"

QUEUE_ROW_SCHEMA = "acu-legislation-19-category-review-queue-row-v1"
WORKSHEET_SCHEMA = "acu-legislation-19-category-candidate-worksheet-v1"
BATCH_SCHEMA = "acu-legislation-19-category-review-batch-v1"
RETIRED_SCHEMA = "acu-legislation-19-category-review-retired-row-v1"
QUERY_DEFINITION_SCHEMA = "acu-legislation-19-category-review-query-definition-v1"
GENERATION_SCHEMA = "acu-legislation-19-category-review-queue-generation-v1"
RECEIPT_SCHEMA = "acu-legislation-19-category-review-queue-receipt-v1"
ADJUDICATION_SCHEMA = "acu-legislation-membership-adjudication-v1"
QUERY_SCHEMA = "congress-advanced-search-query-v1"
TERM_RULESET = "acu-legislation-query-term-extraction-v1"

EXPECTED_CATEGORY_COUNT = 19

CONGRESS_URL_RE = re.compile(r"^https://(?:www\.)?congress\.gov/", re.I)
SHORT_ACT_ALIAS_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9} Act$")
CITE_CONGRESS_RE = re.compile(r"congressId\s*:\s*(\d{3})", re.I)
CITE_MEASURE_RE = re.compile(r"cite\s*:\s*\"?([A-Za-z]+\d+)\"?", re.I)

# Congress.gov search covers bill text and summaries this project does not hold
# locally, so a term that does not match the discovery record is not evidence of
# anything.  Every match block carries this flag so no downstream reader can
# mistake a non-match for a negative finding.
ABSENCE_IS_NEUTRAL = True

RAW_TEXT_SCAN_LIMIT = 20000
MATCH_CONTEXT_CHARS = 160
QUERY_HEADER_MARKER = b'\n  "pages"'
QUERY_HEADER_MAX_BYTES = 4 * 1024 * 1024

# Tier ranks.  Higher is reviewed earlier.  A tier is a statement about signal
# value, never about the likely disposition of the row.
TIER_SCORING_WITH_ACCEPTED_TEXT = 4
TIER_SCORING_CATEGORY = 3
TIER_NON_SCORING_CATEGORY = 2
TIER_NON_DISPOSITIVE_PROBE = 1

TIER_LABELS = {
    TIER_SCORING_WITH_ACCEPTED_TEXT: "T1_scoring_category_with_accepted_full_text",
    TIER_SCORING_CATEGORY: "T2_scoring_category_text_not_accepted",
    TIER_NON_SCORING_CATEGORY: "T3_non_scoring_category",
    TIER_NON_DISPOSITIVE_PROBE: "T4_non_dispositive_probe",
}

CSV_FIELDS = [
    "priority_rank",
    "batch_id",
    "tier",
    "canonical_key",
    "query_id",
    "category_label",
    "scoring_category",
    "membership_non_dispositive",
    "display_number",
    "official_title",
    "matched_terms",
    "explicitly_named_in_query",
    "congress_gov_url",
    "congress_gov_text_url",
    "official_full_text_url",
    "summary_status",
    "full_text_status",
    "blockers",
    "decision_can_finalize_today",
    "queue_row_id",
    # Reviewer columns.  Always blank on emission.
    "disposition",
    "evidence_basis",
    "evidence_url",
    "evidence_quote",
    "reviewer_confidence",
    "reason",
    "reason_code",
    "reviewer",
    "reviewed_at",
]


class ReviewQueueError(ValueError):
    """Raised when the review queue cannot be built without ambiguity."""


# ---------------------------------------------------------------------------
# House primitives
# ---------------------------------------------------------------------------


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def value_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewQueueError(message)


def file_receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    require(resolved.is_file(), f"required input is missing: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def write_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ReviewQueueError(
            f"refusing to overwrite immutable artifact: {path}"
        ) from error


def read_ndjson(path: Path, *, label: str) -> Iterator[tuple[dict[str, Any], str]]:
    # ``iter_ndjson_raw_lines`` strips a leading UTF-8 BOM from line 1 so a
    # PowerShell-written file parses, and so the per-row receipt is the hash of
    # the JSON payload rather than of the encoding marker.
    for line_number, raw_with_newline in iter_ndjson_raw_lines(path):
        if not raw_with_newline.strip():
            continue
        raw = raw_with_newline.rstrip(b"\r\n")
        try:
            row = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReviewQueueError(
                f"{label}:{line_number}: invalid JSON"
            ) from error
        require(
            isinstance(row, dict), f"{label}:{line_number}: row must be an object"
        )
        yield row, sha256_bytes(raw)


class ExclusiveLock:
    """Prevent two writers racing on the same output root."""

    def __init__(self, root: Path):
        self.path = root / ".category-review-queue.lock"
        self.descriptor: int | None = None

    def __enter__(self) -> "ExclusiveLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.descriptor = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
            )
        except FileExistsError as error:
            raise ReviewQueueError(
                f"another queue writer owns {self.path}; inspect it first"
            ) from error
        os.write(self.descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(self.descriptor)
        return self

    def __exit__(self, *_: Any) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
        self.path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Discovery query headers
# ---------------------------------------------------------------------------


def read_query_header(path: Path, *, max_bytes: int = QUERY_HEADER_MAX_BYTES) -> dict[str, Any]:
    """Parse a query file's metadata header from a bounded byte prefix.

    Query result files reach 25 MB.  Only the header is needed here, so the
    file is read in growing chunks until the ``pages`` array begins, and the
    read is hard-capped.  A header that cannot be closed inside the cap fails
    closed rather than being guessed at.
    """

    size = 64 * 1024
    with path.open("rb") as stream:
        while True:
            stream.seek(0)
            chunk = stream.read(min(size, max_bytes))
            index = chunk.find(QUERY_HEADER_MARKER)
            if index >= 0:
                break
            if len(chunk) < size or size >= max_bytes:
                raise ReviewQueueError(
                    f"{path.name}: no metadata header found in the first "
                    f"{len(chunk)} bytes"
                )
            size = min(size * 4, max_bytes)

    body = chunk[:index].rstrip()
    if body.endswith(b","):
        body = body[:-1]
    try:
        header = json.loads(body + b"}")
    except json.JSONDecodeError as error:
        raise ReviewQueueError(f"{path.name}: header is not valid JSON") from error
    require(isinstance(header, dict), f"{path.name}: header is not an object")
    require(
        header.get("schema") == QUERY_SCHEMA,
        f"{path.name}: query schema drift ({header.get('schema')!r})",
    )
    for field in ("query_id", "record_kind", "advanced_search_query"):
        require(
            bool(str(header.get(field) or "").strip()),
            f"{path.name}: header is missing {field}",
        )
    return header


def split_top_level_groups(expression: str) -> list[str]:
    """Return the outermost parenthesised groups of a boolean search string."""

    groups: list[str] = []
    depth = 0
    start = 0
    for index, character in enumerate(expression):
        if character == "(":
            if depth == 0:
                start = index + 1
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                groups.append(expression[start:index])
            elif depth < 0:
                # Unbalanced input: keep what was parsed, never guess.
                depth = 0
    return [group.strip() for group in groups if group.strip()]


def classify_term(text: str) -> str:
    stripped = text.strip()
    if stripped.lower() == "act":
        # Section 3 of the review method: bare "Act" is always non-evidence.
        return "generic_act_non_evidence"
    if SHORT_ACT_ALIAS_RE.fullmatch(stripped):
        # A short acronym alias is a recall probe, never relevance evidence.
        return "short_act_alias_non_dispositive"
    return "phrase"


def extract_query_terms(expression: str) -> list[dict[str, Any]]:
    """Extract reviewable terms from a Congress.gov Advanced Search string.

    Structured ``congressId``/``cite`` clauses are Congress-qualified identity
    anchors and are reported separately from free-text phrases.  Query
    operators (``OR``, ``AND``, quotes, ``~N``) are search syntax and are never
    reported as evidence tokens.
    """

    terms: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in split_top_level_groups(expression):
        if CITE_CONGRESS_RE.search(group) or CITE_MEASURE_RE.search(group):
            congress = CITE_CONGRESS_RE.search(group)
            measure = CITE_MEASURE_RE.search(group)
            identity = " ".join(
                part
                for part in (
                    congress.group(1) if congress else "",
                    measure.group(1).lower() if measure else "",
                )
                if part
            )
            entry = ("cite_identifier", identity)
            if identity and entry not in seen:
                seen.add(entry)
                terms.append(
                    {
                        "kind": "cite_identifier",
                        "text": identity,
                        "raw_clause": group,
                        "is_evidence_token": False,
                        "note": (
                            "Congress-qualified identity anchor from the search "
                            "expression; confirms which measure the query "
                            "targeted, not what the measure does"
                        ),
                    }
                )
            continue

        has_proximity = "~" in group
        candidates: list[str]
        if has_proximity:
            candidates = re.findall(r'"([^"]+)"', group) or [
                re.sub(r"~\s*\d+", " ", group)
            ]
        else:
            candidates = [group]

        for candidate in candidates:
            text = candidate.strip().strip('"').strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                continue
            if text.upper() in {"OR", "AND", "NOT"}:
                continue
            kind = classify_term(text)
            if has_proximity:
                kind = "proximity_component"
            entry = (kind, text.lower())
            if entry in seen:
                continue
            seen.add(entry)
            terms.append(
                {
                    "kind": kind,
                    "text": text,
                    "raw_clause": group,
                    "is_evidence_token": kind == "phrase",
                    "note": (
                        ""
                        if kind == "phrase"
                        else "non-dispositive under the relevance review method"
                    ),
                }
            )
    return terms


CITE_TEXT_RE = re.compile(r"^(\d{3})\s+([a-z]+)(\d+)$")


def cite_anchor_canonical_key(text: str) -> str | None:
    """Resolve a ``congressId``/``cite`` anchor to a canonical measure key.

    ``"119 s2704"`` becomes ``"119:s:2704"``.  A membership whose own measure is
    explicitly named in the query is the one case the relevance review method
    treats specially: an explicitly named, Congress-qualified outline bill.
    """

    match = CITE_TEXT_RE.fullmatch(text.strip().lower())
    if not match:
        return None
    key = f"{match.group(1)}:{match.group(2)}:{int(match.group(3))}"
    return key if CANONICAL_KEY_RE.fullmatch(key) else None


def compile_term(text: str) -> re.Pattern[str]:
    tokens = [re.escape(token) for token in text.split() if token]
    if not tokens:
        return re.compile(r"(?!)")
    body = r"\W+".join(tokens)
    return re.compile(rf"(?<![0-9A-Za-z]){body}(?![0-9A-Za-z])", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Term matching against locally held discovery text
# ---------------------------------------------------------------------------


def match_terms(
    compiled: Sequence[tuple[dict[str, Any], re.Pattern[str]]],
    fields: Mapping[str, str],
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for term, pattern in compiled:
        for field_name, field_value in fields.items():
            if not field_value:
                continue
            found = pattern.search(field_value)
            if found is None:
                continue
            start = max(0, found.start() - MATCH_CONTEXT_CHARS // 2)
            end = min(len(field_value), found.end() + MATCH_CONTEXT_CHARS // 2)
            matches.append(
                {
                    "kind": term["kind"],
                    "text": term["text"],
                    "field": field_name,
                    "offset": found.start(),
                    "span": field_value[start:end].strip(),
                    "is_evidence_token": term["is_evidence_token"],
                }
            )
            break
    return {
        "ruleset": TERM_RULESET,
        "terms_tested": len(compiled),
        "matched_term_count": len(matches),
        "matched_evidence_token_count": sum(
            1 for row in matches if row["is_evidence_token"]
        ),
        "positive_spans": matches,
        "absence_is_neutral": ABSENCE_IS_NEUTRAL,
        "matching_is_navigational_not_evidentiary": True,
    }


MATCH_SEMANTICS_NOTE = (
    "Congress.gov searched official bill text and summaries that this project "
    "does not hold locally. Terms are matched only against the discovery "
    "record's title, purpose, heading, and result blurb. A term that does not "
    "match here has not been ruled out and must never be treated as negative "
    "evidence. Matching is navigational: it tells a reviewer where to look, "
    "never what to decide."
)

EVIDENCE_REQUIREMENTS = {
    "official_title": "required for every disposition",
    "official_crs_summary": (
        "required when Congress.gov publishes one; an officially recorded "
        "absence is neutral and must be cited as such"
    ),
    "official_full_text": "required for every disposition",
    "all_official_text_versions": (
        "required only for an absence-based exclude_false_positive"
    ),
    "press_or_article_absence": "never admissible as exclusion evidence",
    "search_query_membership_alone": "never sufficient to include or exclude",
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


RECORD_FIELDS = (
    "record_kind",
    "congress",
    "type",
    "number",
    "canonical_url",
    "display_number",
    "heading",
    "title",
    "purpose",
    "sponsor",
    "raw_text",
)


def load_records(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for row, _ in read_ndjson(path, label="discovery_records"):
        key = str(row.get("canonical_key") or "")
        if not CANONICAL_KEY_RE.fullmatch(key):
            continue
        require(key not in records, f"duplicate discovery record: {key}")
        records[key] = {field: row.get(field) for field in RECORD_FIELDS}
    require(bool(records), "discovery records are empty")
    return records


def load_memberships(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    memberships: dict[tuple[str, str], dict[str, Any]] = {}
    for row, line_sha in read_ndjson(path, label="discovery_memberships"):
        key = str(row.get("canonical_key") or "")
        query_id = str(row.get("query_id") or "").strip()
        require(
            bool(CANONICAL_KEY_RE.fullmatch(key)),
            f"invalid canonical_key in memberships: {key!r}",
        )
        require(bool(query_id), f"membership for {key} has no query_id")
        natural_key = (key, query_id)
        require(
            natural_key not in memberships,
            f"duplicate discovery membership: {natural_key}",
        )
        memberships[natural_key] = {**row, "_source_membership_sha256": line_sha}
    require(bool(memberships), "membership universe is empty")
    return memberships


EVIDENCE_KEEP = ("canonical_key", "membership_query_ids", "official_evidence")


def load_evidence(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, dict[str, bool]]]:
    evidence: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    readiness: dict[str, dict[str, bool]] = {}
    for row, line_sha in read_ndjson(path, label="candidate_evidence"):
        key = str(row.get("canonical_key") or "")
        require(
            bool(CANONICAL_KEY_RE.fullmatch(key)),
            f"invalid canonical_key in candidate evidence: {key!r}",
        )
        require(key not in evidence, f"duplicate candidate evidence: {key}")
        evidence[key] = row
        hashes[key] = line_sha
        try:
            readiness[key] = validate_candidate_evidence(row)
        except CullingContractError as error:
            raise ReviewQueueError(
                f"{key}: candidate evidence violates the culling contract: {error}"
            ) from error
    require(bool(evidence), "candidate evidence is empty")
    return evidence, hashes, readiness


def load_query_headers(directory: Path) -> dict[str, dict[str, Any]]:
    require(directory.is_dir(), f"queries directory is missing: {directory}")
    headers: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        header = read_query_header(path)
        query_id = str(header["query_id"])
        require(query_id not in headers, f"duplicate query_id: {query_id}")
        header["_file"] = str(path.resolve())
        header["_file_sha256"] = sha256_file(path)
        header["_file_bytes"] = path.stat().st_size
        headers[query_id] = header
    require(bool(headers), f"no query files found in {directory}")
    return headers


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------


def navigation_links(record: Mapping[str, Any]) -> dict[str, Any]:
    canonical_url = str(record.get("canonical_url") or "")
    verified = bool(CONGRESS_URL_RE.match(canonical_url))
    is_bill = str(record.get("record_kind") or "") == "bill"
    return {
        "canonical_url": canonical_url if verified else None,
        "canonical_url_verified": verified,
        "text_url": f"{canonical_url}/text" if (verified and is_bill) else None,
        "all_info_url": f"{canonical_url}/all-info" if (verified and is_bill) else None,
        # Derived for navigation only.  They carry no collection receipt, and
        # congress.gov amendment pages expose no /text surface at all.
        "derived_urls_verified": False,
    }


def candidate_blockers(
    readiness: Mapping[str, bool],
    official: Mapping[str, Any],
) -> list[str]:
    """Name every reason this membership cannot finalize, evidence-side.

    A blocker is a statement about missing evidence.  It is never a reason to
    exclude and never a disposition.
    """

    blockers: list[str] = []
    if not readiness.get("category_audit_ready"):
        blockers.append("candidate_19_category_audit_absent")
    if not readiness.get("title_ready"):
        blockers.append("official_title_not_congress_gov_verified")
    if not (
        readiness.get("summary_ready")
        or readiness.get("summary_explicitly_unavailable")
    ):
        blockers.append("official_crs_summary_neither_fetched_nor_officially_absent")
    if not readiness.get("full_text_ready"):
        blockers.append("official_full_text_not_accepted_from_congress_gov")
    inventory = (official.get("full_text") or {}).get("version_inventory") or {}
    if inventory.get("all_versions_reviewed") is not True:
        blockers.append("official_text_version_inventory_incomplete_blocks_exclusion")
    return blockers


def build_queue_row(
    *,
    canonical_key: str,
    query_id: str,
    membership: Mapping[str, Any],
    query: Mapping[str, Any],
    record: Mapping[str, Any],
    evidence: Mapping[str, Any],
    evidence_sha256: str,
    readiness: Mapping[str, bool],
    compiled_terms: Sequence[tuple[dict[str, Any], re.Pattern[str]]],
    candidate_summary: Mapping[str, Any],
) -> dict[str, Any]:
    official = evidence.get("official_evidence") or {}
    title_block = official.get("title") or {}
    summary_block = official.get("summary") or {}
    full_text_block = official.get("full_text") or {}

    official_title = official_title_of(record)
    raw_text = str(record.get("raw_text") or "")[:RAW_TEXT_SCAN_LIMIT]
    matched = match_terms(
        compiled_terms,
        {
            "title": str(record.get("title") or ""),
            "purpose": str(record.get("purpose") or ""),
            "heading": str(record.get("heading") or ""),
            "raw_text": raw_text,
        },
    )

    blockers = candidate_blockers(readiness, official)
    # A decision recorded today can only clear the gate if the 19-category
    # audit is the *only* thing missing.  Everything else is a collection gap
    # that no amount of reviewing can close.
    non_audit_blockers = [
        name for name in blockers if name != "candidate_19_category_audit_absent"
    ]

    links = navigation_links(record)
    cite_keys = {
        resolved
        for term, _ in compiled_terms
        if term["kind"] == "cite_identifier"
        and (resolved := cite_anchor_canonical_key(term["text"])) is not None
    }
    explicitly_named = canonical_key in cite_keys
    row_identity = {
        "canonical_key": canonical_key,
        "query_id": query_id,
        "source_membership_sha256": membership["_source_membership_sha256"],
        "candidate_evidence_sha256": evidence_sha256,
        "query_sha256": query.get("query_sha256"),
        "tool_version": TOOL_VERSION,
    }

    return {
        "schema": QUEUE_ROW_SCHEMA,
        "queue_row_id": "qr-" + value_sha256(row_identity)[:32],
        "decision_unit": "membership",
        "canonical_key": canonical_key,
        "query_id": query_id,
        "adjudication": {
            "state": "unresolved",
            "disposition": None,
            "human_ratification_required": True,
            "automated_final_decision_permitted": False,
        },
        "membership_cause": {
            "label": membership.get("label"),
            "record_kind": membership.get("record_kind"),
            "scoring_category": bool(membership.get("scoring_category")),
            "membership_non_dispositive": bool(
                membership.get("membership_non_dispositive")
            ),
            "parent_category_id": membership.get("parent_category_id"),
            "parent_category_label": membership.get("parent_category_label"),
            "identifier_text": membership.get("identifier_text"),
            "title_alias": membership.get("title_alias"),
            "ambiguous_short_act_alias": bool(
                membership.get("ambiguous_short_act_alias")
            ),
            "source_paragraph": membership.get("source_paragraph"),
            "source_page": membership.get("source_page"),
            "source_rank": membership.get("source_rank"),
            "result_url": membership.get("result_url"),
            "source_membership_sha256": membership["_source_membership_sha256"],
        },
        # The full search expression, search URL, and extracted term list live
        # once in queries.ndjson.  Repeating a 2.5 KB search URL on every one of
        # 64,004 rows would bloat the queue without adding information.
        "query": {
            "query_id": query_id,
            "label": query.get("label"),
            "record_kind": query.get("record_kind"),
            "scoring_category": bool(query.get("scoring_category")),
            "parent_category_id": query.get("parent_category_id"),
            "query_sha256": query.get("query_sha256"),
            "registry_sha256": query.get("registry_sha256"),
            "query_file_sha256": query.get("_file_sha256"),
            "full_definition_in": "queries.ndjson",
        },
        "query_terms": {
            # Whether *this* measure is one of the Congress-qualified measures
            # the query names outright.  The query's full anchor list lives in
            # queries.ndjson; repeating it per row would be query-level noise.
            "candidate_explicitly_named_in_query": explicitly_named,
            "query_names_measures_explicitly": bool(cite_keys),
            "match_against_discovery_record": matched,
        },
        # Candidate-level facts live once in candidate_worksheets.ndjson.  Only
        # the fields a reviewer needs to recognise the measure without a second
        # lookup are repeated here.
        "candidate": {
            "display_number": record.get("display_number"),
            "official_title": official_title or None,
            "canonical_url": links["canonical_url"],
            "official_full_text_url": (
                full_text_block.get("expected_url")
                if full_text_block.get("status") == "accepted"
                else None
            ),
            "summary_status": summary_block.get("status"),
            "full_text_status": full_text_block.get("status"),
            "candidate_evidence_sha256": evidence_sha256,
            "membership_count": candidate_summary["membership_count"],
            "scoring_category_count": candidate_summary["scoring_category_count"],
            "details_in": "candidate_worksheets.ndjson",
        },
        "evidence_blockers": blockers,
        "decision_can_finalize_today": not non_audit_blockers,
        "safety": {
            "disposition_assigned": False,
            "category_evidence_written": False,
        },
    }


def official_title_of(record: Mapping[str, Any]) -> str:
    for field in ("title", "purpose", "heading"):
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return ""


# ---------------------------------------------------------------------------
# Prioritisation
# ---------------------------------------------------------------------------


def membership_tier(
    *,
    query: Mapping[str, Any],
    membership_non_dispositive: bool,
    full_text_accepted: bool,
) -> int:
    if str(query.get("record_kind")) != "category" or membership_non_dispositive:
        return TIER_NON_DISPOSITIVE_PROBE
    if not query.get("scoring_category"):
        return TIER_NON_SCORING_CATEGORY
    return (
        TIER_SCORING_WITH_ACCEPTED_TEXT
        if full_text_accepted
        else TIER_SCORING_CATEGORY
    )


def candidate_sort_key(summary: Mapping[str, Any]) -> tuple:
    """Deterministic total order over candidates, best work first.

    Every component is explicit so a reviewer can read the queue order without
    reading this file:

    1. the candidate's best tier;
    2. how many queries name this measure outright by Congress-qualified cite;
    3. how many scoring categories it appears in (breadth);
    4. how many memberships it carries;
    5. whether Congress.gov full text has been accepted;
    6. whether the CRS summary question is settled;
    7. canonical_key, purely to break ties deterministically.
    """

    return (
        -summary["candidate_tier"],
        -summary["explicitly_named_count"],
        -summary["scoring_category_count"],
        -summary["membership_count"],
        -int(summary["full_text_accepted"]),
        -int(summary["summary_decidable"]),
        summary["canonical_key"],
    )


def membership_sort_key(row: Mapping[str, Any]) -> tuple:
    return (
        -row["_tier"],
        -int(row["query_terms"]["candidate_explicitly_named_in_query"]),
        -int(row["membership_cause"]["ambiguous_short_act_alias"]),
        row["query_id"],
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _assert_inputs_outside_root(root: Path, inputs: Mapping[str, Any]) -> None:
    for name, receipt in inputs.items():
        if not receipt:
            continue
        paths: Iterable[str]
        if isinstance(receipt, Mapping) and "path" in receipt:
            paths = [str(receipt["path"])]
        elif isinstance(receipt, list):
            paths = [str(item.get("path")) for item in receipt if item]
        else:
            continue
        for raw in paths:
            try:
                Path(raw).resolve().relative_to(root)
            except ValueError:
                continue
            raise ReviewQueueError(
                f"{name} is inside the output root; inputs must remain immutable"
            )


def _verify_evidence_binding(
    evidence_path: Path,
    receipt_path: Path | None,
    records_receipt: Mapping[str, Any],
    memberships_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Refuse to queue evidence built from different discovery files.

    Multiple candidate-evidence generations exist on this machine, built from
    different copies of the discovery corpus.  Mixing them would show reviewers
    a membership set that does not match the corpus being culled.
    """

    if receipt_path is None:
        sibling = evidence_path.parent / "receipt.json"
        receipt_path = sibling if sibling.is_file() else None
    if receipt_path is None:
        return {
            "verified": False,
            "reason": "no candidate-evidence receipt supplied or found alongside",
        }
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    inputs = receipt.get("inputs") or {}
    declared_records = (inputs.get("discovery_records") or {}).get("sha256")
    declared_memberships = (inputs.get("discovery_memberships") or {}).get("sha256")
    require(
        declared_records == records_receipt["sha256"],
        "candidate evidence was built from a different discovery records file "
        f"({declared_records} != {records_receipt['sha256']})",
    )
    require(
        declared_memberships == memberships_receipt["sha256"],
        "candidate evidence was built from a different discovery memberships "
        f"file ({declared_memberships} != {memberships_receipt['sha256']})",
    )
    return {
        "verified": True,
        "receipt_path": str(receipt_path.resolve()),
        "receipt_sha256": sha256_file(receipt_path),
        "declared_discovery_records_sha256": declared_records,
        "declared_discovery_memberships_sha256": declared_memberships,
    }


def _load_supplied_decisions(
    paths: Sequence[Path],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    counters = {"rows_read": 0, "adjudication_rows": 0, "non_adjudication_rows": 0}
    receipts: list[dict[str, Any]] = []
    for path in paths:
        receipts.append(file_receipt(path))
        for row, _ in read_ndjson(path, label=f"decisions:{path.name}"):
            counters["rows_read"] += 1
            if row.get("schema") != ADJUDICATION_SCHEMA:
                counters["non_adjudication_rows"] += 1
                continue
            key = str(row.get("canonical_key") or "")
            query_id = str(row.get("query_id") or "").strip()
            if not CANONICAL_KEY_RE.fullmatch(key) or not query_id:
                counters["non_adjudication_rows"] += 1
                continue
            counters["adjudication_rows"] += 1
            decisions[(key, query_id)] = row
    return decisions, counters, receipts


def build(
    *,
    records_path: Path,
    memberships_path: Path,
    queries_dir: Path,
    evidence_path: Path,
    root: Path,
    evidence_receipt_path: Path | None = None,
    decision_paths: Sequence[Path] = (),
    batch_size: int = 250,
    limit: int | None = None,
    event_time: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    records_receipt = file_receipt(records_path)
    memberships_receipt = file_receipt(memberships_path)
    evidence_receipt = file_receipt(evidence_path)
    decision_paths = [Path(item).resolve() for item in decision_paths]
    decisions, decision_counters, decision_receipts = _load_supplied_decisions(
        decision_paths
    )

    inputs: dict[str, Any] = {
        "discovery_records": records_receipt,
        "discovery_memberships": memberships_receipt,
        "candidate_evidence": evidence_receipt,
        "membership_decisions": decision_receipts,
    }
    _assert_inputs_outside_root(root, inputs)

    binding = _verify_evidence_binding(
        evidence_path.resolve(),
        evidence_receipt_path.resolve() if evidence_receipt_path else None,
        records_receipt,
        memberships_receipt,
    )

    queries = load_query_headers(queries_dir)
    category_query_ids = sorted(
        query_id
        for query_id, header in queries.items()
        if header.get("record_kind") == "category"
    )
    require(
        len(category_query_ids) == EXPECTED_CATEGORY_COUNT,
        f"expected {EXPECTED_CATEGORY_COUNT} category queries, found "
        f"{len(category_query_ids)}",
    )

    records = load_records(records_path)
    memberships = load_memberships(memberships_path)
    evidence, evidence_hashes, readiness = load_evidence(evidence_path)

    compiled_by_query: dict[str, list[tuple[dict[str, Any], re.Pattern[str]]]] = {}
    for query_id, header in queries.items():
        terms = extract_query_terms(str(header.get("advanced_search_query") or ""))
        compiled_by_query[query_id] = [
            (term, compile_term(term["text"])) for term in terms
        ]

    explicit_named_by_query: dict[str, set[str]] = {
        query_id: {
            resolved
            for term, _ in terms
            if term["kind"] == "cite_identifier"
            and (resolved := cite_anchor_canonical_key(term["text"])) is not None
        }
        for query_id, terms in compiled_by_query.items()
    }

    discovered_universe: dict[str, set[str]] = {}
    for key, query_id in memberships:
        discovered_universe.setdefault(key, set()).add(query_id)

    for key, query_ids in discovered_universe.items():
        require(key in records, f"membership candidate has no discovery record: {key}")
        require(key in evidence, f"membership candidate has no evidence envelope: {key}")
        declared = evidence[key].get("membership_query_ids") or []
        require(
            set(declared) == query_ids and len(set(declared)) == len(declared),
            f"{key}: candidate evidence membership set does not match discovery",
        )

    for _, query_id in memberships:
        require(query_id in queries, f"membership names an unknown query: {query_id}")

    candidate_summaries: dict[str, dict[str, Any]] = {}
    for key, query_ids in discovered_universe.items():
        official = evidence[key].get("official_evidence") or {}
        full_text_accepted = bool(readiness[key].get("full_text_ready"))
        summary_decidable = bool(
            readiness[key].get("summary_ready")
            or readiness[key].get("summary_explicitly_unavailable")
        )
        scoring = sorted(
            query_id
            for query_id in query_ids
            if queries[query_id].get("record_kind") == "category"
            and queries[query_id].get("scoring_category")
            and not memberships[(key, query_id)].get("membership_non_dispositive")
        )
        tiers = [
            membership_tier(
                query=queries[query_id],
                membership_non_dispositive=bool(
                    memberships[(key, query_id)].get("membership_non_dispositive")
                ),
                full_text_accepted=full_text_accepted,
            )
            for query_id in sorted(query_ids)
        ]
        candidate_summaries[key] = {
            "canonical_key": key,
            "membership_count": len(query_ids),
            "membership_query_ids": sorted(query_ids),
            "scoring_category_count": len(scoring),
            "scoring_query_ids": scoring,
            "full_text_accepted": full_text_accepted,
            "summary_decidable": summary_decidable,
            "explicitly_named_count": sum(
                1
                for query_id in query_ids
                if key in explicit_named_by_query.get(query_id, ())
            ),
            "summary_status": (official.get("summary") or {}).get("status"),
            "full_text_status": (official.get("full_text") or {}).get("status"),
            "candidate_tier": max(tiers) if tiers else TIER_NON_DISPOSITIVE_PROBE,
        }

    # ---- retire rows already finalizable under the project's own contract ---
    retired: list[dict[str, Any]] = []
    pending: dict[str, list[dict[str, Any]]] = {}
    tier_counts: dict[str, int] = {label: 0 for label in TIER_LABELS.values()}
    blocker_counts: dict[str, int] = {}
    prior_decision_not_final = 0

    for natural_key in sorted(memberships):
        key, query_id = natural_key
        supplied = decisions.get(natural_key)
        if supplied is not None and membership_is_finalizable(supplied, evidence[key]):
            retired.append(
                {
                    "schema": RETIRED_SCHEMA,
                    "canonical_key": key,
                    "query_id": query_id,
                    "reason": "membership_is_finalizable accepted a supplied decision",
                    "disposition": supplied.get("disposition"),
                    "adjudication_state": supplied.get("adjudication_state"),
                    "reviewer": supplied.get("reviewer"),
                    "reviewed_at": supplied.get("reviewed_at"),
                    "decision_evidence_sha256": supplied.get(
                        "decision_evidence_sha256"
                    ),
                    "retired_by_this_tool": False,
                }
            )
            continue

        summary = candidate_summaries[key]
        membership = memberships[natural_key]
        query = queries[query_id]
        # The query registry is the authority for what a query *is*.  A
        # membership row that disagrees with it means two generations were
        # mixed, and the reviewer would be shown the wrong category.
        require(
            str(membership.get("record_kind") or "")
            == str(query.get("record_kind") or "")
            and bool(membership.get("scoring_category"))
            == bool(query.get("scoring_category")),
            f"{natural_key}: membership metadata disagrees with the query registry",
        )
        row = build_queue_row(
            canonical_key=key,
            query_id=query_id,
            membership=membership,
            query=query,
            record=records[key],
            evidence=evidence[key],
            evidence_sha256=evidence_hashes[key],
            readiness=readiness[key],
            compiled_terms=compiled_by_query[query_id],
            candidate_summary=summary,
        )
        tier = membership_tier(
            query=query,
            membership_non_dispositive=bool(
                membership.get("membership_non_dispositive")
            ),
            full_text_accepted=summary["full_text_accepted"],
        )
        row["_tier"] = tier
        row["tier"] = TIER_LABELS[tier]
        row["tier_rank"] = tier
        if supplied is not None:
            prior_decision_not_final += 1
            row["adjudication"]["state"] = "unresolved_prior_decision_not_finalizable"
            row["adjudication"]["reason"] = (
                "a decision exists but membership_is_finalizable rejected it "
                "against the current official evidence"
            )
            row["prior_decision"] = {
                "disposition": supplied.get("disposition"),
                "adjudication_state": supplied.get("adjudication_state"),
                "reviewer": supplied.get("reviewer"),
                "reviewed_at": supplied.get("reviewed_at"),
                "decision_source": supplied.get("decision_source"),
            }
        tier_counts[TIER_LABELS[tier]] += 1
        for blocker in row["evidence_blockers"]:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        pending.setdefault(key, []).append(row)

    total_pending = sum(len(rows) for rows in pending.values())

    ordered_candidates = sorted(
        (candidate_summaries[key] for key in pending),
        key=candidate_sort_key,
    )

    queue_rows: list[dict[str, Any]] = []
    worksheets: list[dict[str, Any]] = []
    batches: list[dict[str, Any]] = []
    batch_index = 0
    batch_rows = 0
    batch_candidates = 0
    batch_first_rank = 1
    limit_applied = False

    for summary in ordered_candidates:
        key = summary["canonical_key"]
        rows = sorted(pending[key], key=membership_sort_key)
        if limit is not None and queue_rows and len(queue_rows) + len(rows) > limit:
            limit_applied = True
            break
        if batch_rows and batch_rows + len(rows) > batch_size:
            batches.append(
                {
                    "schema": BATCH_SCHEMA,
                    "batch_id": f"batch-{batch_index:05d}",
                    "candidate_count": batch_candidates,
                    "row_count": batch_rows,
                    "first_priority_rank": batch_first_rank,
                    "last_priority_rank": len(queue_rows),
                }
            )
            batch_index += 1
            batch_rows = 0
            batch_candidates = 0
            batch_first_rank = len(queue_rows) + 1
        batch_id = f"batch-{batch_index:05d}"
        first_rank = len(queue_rows) + 1
        for row in rows:
            row.pop("_tier", None)
            row["priority_rank"] = len(queue_rows) + 1
            row["batch_id"] = batch_id
            row["candidate_tier"] = TIER_LABELS[summary["candidate_tier"]]
            queue_rows.append(row)
        batch_rows += len(rows)
        batch_candidates += 1

        record = records[key]
        official = evidence[key].get("official_evidence") or {}
        title_block = official.get("title") or {}
        full_text_block = official.get("full_text") or {}
        member_set = set(summary["membership_query_ids"])
        worksheets.append(
            {
                "schema": WORKSHEET_SCHEMA,
                "canonical_key": key,
                "display_number": record.get("display_number"),
                "official_title": official_title_of(record) or None,
                "official_title_status": title_block.get("status"),
                "official_title_text_sha256": title_block.get("text_sha256"),
                "record_kind": record.get("record_kind"),
                "congress": record.get("congress"),
                "measure_type": record.get("type"),
                "number": record.get("number"),
                "sponsor": record.get("sponsor"),
                "links": navigation_links(record),
                "official_full_text_url": (
                    full_text_block.get("expected_url")
                    if full_text_block.get("status") == "accepted"
                    else None
                ),
                "official_text_version_inventory": full_text_block.get(
                    "version_inventory"
                ),
                "stage_status": (evidence[key].get("stage") or {}).get("status"),
                "readiness_snapshot": dict(readiness[key]),
                "priority_basis": {
                    "candidate_tier": TIER_LABELS[summary["candidate_tier"]],
                    "scoring_category_count": summary["scoring_category_count"],
                    "membership_count": summary["membership_count"],
                    "full_text_accepted": summary["full_text_accepted"],
                    "summary_decidable": summary["summary_decidable"],
                    "explicitly_named_count": summary["explicitly_named_count"],
                },
                "batch_id": batch_id,
                "first_priority_rank": first_rank,
                "queue_row_count": len(rows),
                "membership_query_ids": summary["membership_query_ids"],
                "scoring_query_ids": summary["scoring_query_ids"],
                "candidate_evidence_sha256": evidence_hashes[key],
                "summary_status": summary["summary_status"],
                "full_text_status": summary["full_text_status"],
                # The evidence envelope requires exactly 19 category audit rows
                # per candidate, including the categories the candidate is not
                # a discovered member of.  All 19 slots are enumerated here so
                # the per-candidate sweep and the per-membership decisions
                # cannot drift apart.  Labels, scoring flags, and the required
                # field list are constant across candidates and live once in
                # manifest.json:category_registry.
                "category_audit_slots": [
                    {
                        "category_id": category_id,
                        "is_discovered_membership": category_id in member_set,
                        "state": "not_performed",
                    }
                    for category_id in category_query_ids
                ],
                "category_audit_slot_count": len(category_query_ids),
            }
        )

    if batch_rows:
        batches.append(
            {
                "schema": BATCH_SCHEMA,
                "batch_id": f"batch-{batch_index:05d}",
                "candidate_count": batch_candidates,
                "row_count": batch_rows,
                "first_priority_rank": batch_first_rank,
                "last_priority_rank": len(queue_rows),
            }
        )

    query_rows = [
        {
            "schema": QUERY_DEFINITION_SCHEMA,
            "query_id": query_id,
            "label": header.get("label"),
            "record_kind": header.get("record_kind"),
            "scoring_category": bool(header.get("scoring_category")),
            "membership_non_dispositive": bool(
                header.get("membership_non_dispositive")
            ),
            "parent_category_id": header.get("parent_category_id"),
            "title_alias": header.get("title_alias"),
            "ambiguous_short_act_alias": bool(
                header.get("ambiguous_short_act_alias")
            ),
            "advanced_search_query": header.get("advanced_search_query"),
            "search_url": header.get("search_url"),
            "query_sha256": header.get("query_sha256"),
            "registry_sha256": header.get("registry_sha256"),
            "result_total": header.get("result_total"),
            "unique_record_count": header.get("unique_record_count"),
            "query_file": header.get("_file"),
            "query_file_sha256": header.get("_file_sha256"),
            "query_file_bytes": header.get("_file_bytes"),
            "extracted_terms": [
                {key: value for key, value in term.items() if key != "raw_clause"}
                for term, _ in compiled_by_query[query_id]
            ],
            "term_ruleset": TERM_RULESET,
        }
        for query_id, header in sorted(queries.items())
    ]

    bodies = {
        "queries.ndjson": b"".join(
            canonical_json_bytes(row) + b"\n" for row in query_rows
        ),
        "queue.ndjson": b"".join(
            canonical_json_bytes(row) + b"\n" for row in queue_rows
        ),
        "candidate_worksheets.ndjson": b"".join(
            canonical_json_bytes(row) + b"\n" for row in worksheets
        ),
        "batches.ndjson": b"".join(
            canonical_json_bytes(row) + b"\n" for row in batches
        ),
        "retired_rows.ndjson": b"".join(
            canonical_json_bytes(row) + b"\n" for row in retired
        ),
        "queue.csv": _queue_csv_body(
            queue_rows, {row["canonical_key"]: row for row in worksheets}
        ),
    }
    record_counts = {
        "queries.ndjson": len(query_rows),
        "queue.ndjson": len(queue_rows),
        "candidate_worksheets.ndjson": len(worksheets),
        "batches.ndjson": len(batches),
        "retired_rows.ndjson": len(retired),
        "queue.csv": len(queue_rows),
    }

    parameters = {
        "batch_size": int(batch_size),
        "limit": int(limit) if limit is not None else None,
        "expected_category_count": EXPECTED_CATEGORY_COUNT,
        "term_ruleset": TERM_RULESET,
        "raw_text_scan_limit": RAW_TEXT_SCAN_LIMIT,
    }
    generation_basis = {
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "inputs": inputs,
        "queries": {
            "directory": str(queries_dir.resolve()),
            "query_count": len(queries),
            "category_query_ids": category_query_ids,
            "query_file_sha256s": {
                query_id: header["_file_sha256"]
                for query_id, header in sorted(queries.items())
            },
        },
        "parameters": parameters,
        "queue_rows_sha256": sha256_bytes(bodies["queue.ndjson"]),
        "worksheets_sha256": sha256_bytes(bodies["candidate_worksheets.ndjson"]),
        "retired_sha256": sha256_bytes(bodies["retired_rows.ndjson"]),
        "query_definitions_sha256": sha256_bytes(bodies["queries.ndjson"]),
    }
    queue_id = value_sha256(generation_basis)

    counts = {
        "discovery_memberships": len(memberships),
        "discovery_candidates": len(discovered_universe),
        "rows_needing_adjudication": total_pending,
        "rows_emitted": len(queue_rows),
        "candidates_emitted": len(worksheets),
        "batches": len(batches),
        "rows_retired_by_existing_finalizable_decision": len(retired),
        "rows_with_prior_non_finalizable_decision": prior_decision_not_final,
        "rows_that_can_finalize_after_adjudication_alone": sum(
            1 for row in queue_rows if row["decision_can_finalize_today"]
        ),
        "rows_where_candidate_is_explicitly_named_by_the_query": sum(
            1
            for row in queue_rows
            if row["query_terms"]["candidate_explicitly_named_in_query"]
        ),
        "rows_with_ambiguous_short_act_alias": sum(
            1
            for row in queue_rows
            if row["membership_cause"]["ambiguous_short_act_alias"]
        ),
        "rows_with_a_local_query_term_match": sum(
            1
            for row in queue_rows
            if row["query_terms"]["match_against_discovery_record"][
                "matched_term_count"
            ]
        ),
        "tier_counts": tier_counts,
        "evidence_blocker_counts": dict(sorted(blocker_counts.items())),
        "category_audit_rows_required": len(worksheets) * EXPECTED_CATEGORY_COUNT,
        "supplied_decision_rows": decision_counters,
        "limit_applied": limit_applied,
    }

    resolved_event_time = event_time or utc_now()
    # ``event_time`` is deliberately absent from the sealed receipt.  It is
    # per-invocation metadata, not generation state: an identical rerun
    # describes the same generation and must not collide with it.  It is
    # preserved per invocation under ``invocations/`` and returned to the
    # caller below.  This is the same lesson the reversible culling engine
    # learned with ``new_junk_exclusions_appended``.
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS_CATEGORY_REVIEW_QUEUE_BUILT",
        "queue_id": queue_id,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "inputs": inputs,
        "candidate_evidence_binding": binding,
        "queries": generation_basis["queries"],
        "parameters": parameters,
        "counts": counts,
        "evidence_requirements": EVIDENCE_REQUIREMENTS,
        "term_match_semantics": MATCH_SEMANTICS_NOTE,
        "category_registry": [
            {
                "category_id": category_id,
                "label": queries[category_id].get("label"),
                "scoring_category": bool(queries[category_id].get("scoring_category")),
                "query_sha256": queries[category_id].get("query_sha256"),
            }
            for category_id in category_query_ids
        ],
        "category_audit_required_fields": [
            "title_reviewed",
            "summary_status_reviewed",
            "full_text_reviewed",
            "absence_is_neutral",
            "evidence_sha256",
        ],
        "tier_definitions": {
            TIER_LABELS[TIER_SCORING_WITH_ACCEPTED_TEXT]: (
                "scoring category, and Congress.gov full text is accepted and "
                "physically reverified for this candidate"
            ),
            TIER_LABELS[TIER_SCORING_CATEGORY]: (
                "scoring category, but no accepted Congress.gov full text yet"
            ),
            TIER_LABELS[TIER_NON_SCORING_CATEGORY]: (
                "a category query that is deliberately non-scoring"
            ),
            TIER_LABELS[TIER_NON_DISPOSITIVE_PROBE]: (
                "coverage probe or membership_non_dispositive edge; membership "
                "alone can never establish relevance"
            ),
        },
        "outputs": {},
        "gates": {
            "driven_off_discovery_partition_not_effective_ledger": True,
            "every_discovered_membership_accounted_for": (
                counts["rows_needing_adjudication"]
                + counts["rows_retired_by_existing_finalizable_decision"]
                == counts["discovery_memberships"]
            ),
            "nineteen_category_queries_present": True,
            "missing_evidence_recorded_as_blocker_never_exclusion": True,
            "queue_rows_are_all_unresolved": all(
                row["adjudication"]["disposition"] is None for row in queue_rows
            ),
            "deterministic_total_order": True,
        },
        "safety": {
            "adjudication_performed": False,
            "category_evidence_written": False,
            "disposition_assigned": False,
            "keep_or_junk_marked": False,
            "culling_performed": False,
            "network_request_performed": False,
            "scoring_performed": False,
            "sync_or_promotion_performed": False,
            "main_corpus_write_performed": False,
        },
    }

    with ExclusiveLock(root):
        generation_root = root / "queues" / queue_id
        receipt["outputs"] = {
            name: {
                "path": str((generation_root / name).resolve()),
                "bytes": len(body),
                "sha256": sha256_bytes(body),
                "record_count": record_counts[name],
            }
            for name, body in sorted(bodies.items())
        }
        manifest = {
            "schema": GENERATION_SCHEMA,
            "queue_id": queue_id,
            "receipt": receipt,
            "generation_basis": generation_basis,
        }
        manifest_body = canonical_json_bytes(manifest) + b"\n"

        if generation_root.exists():
            manifest_path = generation_root / "manifest.json"
            if (
                not manifest_path.is_file()
                or manifest_path.read_bytes() != manifest_body
            ):
                raise ReviewQueueError(
                    f"immutable queue generation collision: {generation_root}"
                )
            for name, body in bodies.items():
                if (generation_root / name).read_bytes() != body:
                    raise ReviewQueueError(
                        f"immutable queue artifact drift: {name}"
                    )
        else:
            generation_root.mkdir(parents=True, exist_ok=False)
            for name, body in sorted(bodies.items()):
                write_new(generation_root / name, body)
            write_new(generation_root / "manifest.json", manifest_body)

        invocation = {
            "schema": GENERATION_SCHEMA + "-invocation",
            "queue_id": queue_id,
            "event_time": resolved_event_time,
            "decision_inputs": [item["sha256"] for item in decision_receipts],
        }
        invocation_body = canonical_json_bytes(invocation) + b"\n"
        invocation_path = (
            generation_root / "invocations" / f"{sha256_bytes(invocation_body)}.json"
        )
        if not invocation_path.exists():
            write_new(invocation_path, invocation_body)

    for name, original in (
        ("discovery_records", records_receipt),
        ("discovery_memberships", memberships_receipt),
        ("candidate_evidence", evidence_receipt),
    ):
        current = file_receipt(Path(original["path"]))
        if current["sha256"] != original["sha256"]:
            raise ReviewQueueError(f"immutable input changed during build: {name}")

    return {
        **manifest,
        "receipt": {**receipt, "event_time": resolved_event_time},
    }


def _queue_csv_body(
    rows: Sequence[Mapping[str, Any]],
    worksheets_by_key: Mapping[str, Mapping[str, Any]],
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        cause = row["membership_cause"]
        candidate = row["candidate"]
        worksheet = worksheets_by_key.get(row["canonical_key"], {})
        matched = row["query_terms"]["match_against_discovery_record"]
        writer.writerow(
            {
                "priority_rank": row["priority_rank"],
                "batch_id": row["batch_id"],
                "tier": row["tier"],
                "canonical_key": row["canonical_key"],
                "query_id": row["query_id"],
                "category_label": cause.get("label") or "",
                "scoring_category": cause["scoring_category"],
                "membership_non_dispositive": cause["membership_non_dispositive"],
                "display_number": candidate.get("display_number") or "",
                "official_title": candidate.get("official_title") or "",
                "matched_terms": "; ".join(
                    sorted(
                        {
                            span["text"]
                            for span in matched["positive_spans"]
                            if span["kind"] != "cite_identifier"
                        }
                    )
                ),
                "explicitly_named_in_query": row["query_terms"][
                    "candidate_explicitly_named_in_query"
                ],
                # The exact highlighted search-result URL is ~900 bytes of
                # re-encoded query string.  It stays in queue.ndjson as
                # provenance; the spreadsheet carries the clickable measure page.
                "congress_gov_url": candidate.get("canonical_url") or "",
                "congress_gov_text_url": (worksheet.get("links") or {}).get("text_url")
                or "",
                "official_full_text_url": candidate.get("official_full_text_url")
                or "",
                "summary_status": candidate.get("summary_status") or "",
                "full_text_status": candidate.get("full_text_status") or "",
                "blockers": ";".join(row["evidence_blockers"]),
                "decision_can_finalize_today": row["decision_can_finalize_today"],
                "queue_row_id": row["queue_row_id"],
                "disposition": "",
                "evidence_basis": "",
                "evidence_url": "",
                "evidence_quote": "",
                "reviewer_confidence": "",
                "reason": "",
                "reason_code": "",
                "reviewer": "",
                "reviewed_at": "",
            }
        )
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--memberships", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--evidence-receipt", type=Path, default=None)
    parser.add_argument("--decisions", type=Path, action="append", default=[])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--event-time", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = build(
            records_path=args.records,
            memberships_path=args.memberships,
            queries_dir=args.queries.resolve(),
            evidence_path=args.evidence,
            evidence_receipt_path=args.evidence_receipt,
            decision_paths=args.decisions,
            root=args.root,
            batch_size=args.batch_size,
            limit=args.limit,
            event_time=args.event_time,
        )
    except (ReviewQueueError, CullingContractError, OSError, json.JSONDecodeError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest["receipt"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
