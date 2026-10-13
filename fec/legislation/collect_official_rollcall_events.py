# -*- coding: utf-8 -*-
"""Collect official, type-aware roll-call evidence into an audit staging directory.

This module is deliberately separate from ``vote_by_category.py`` and never writes
``vote_category_lean.json``.  Its authority chain is:

1. an explicitly accepted canonical measure/category ledger;
2. the measure's complete official action history, from either the Congress.gov
   API or type-aware GovInfo/Congress.gov BILLSTATUS bulk XML;
3. the recorded-vote association and immutable event key in that action; and
4. the official House Clerk or Senate roll-call XML for that event key.

Nothing is aggregated unless the action and chamber XML both identify a final
measure disposition, the immutable event key agrees, the official XML identifies
the exact type-aware accepted measure, the member resolves to a Bioguide ID, and
the category has an explicit, provenance-bearing direction.

All candidates remain visible.  The event ledger uses the dispositions
``included``, ``excluded``, ``unmatched``, and ``ambiguous``; the member-vote
ledger applies the same vocabulary.  Missing or contradictory official evidence
therefore produces audit artifacts but a failing manifest/exit status.

Bills and resolutions use official BILLSTATUS bulk XML by default and therefore
do not require ``CONGRESS_API_KEY``.  BILLSTATUS does not publish amendment
records, so accepted House or Senate amendments require either the Congress.gov
API key or an exhaustive offline fixture.  Offline tests and reproducible review
use ``--fixture-manifest``; see ``FixtureFetcher`` for its small JSON contract.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import collections
import csv
import dataclasses
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from typing import Any, Iterable
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from legislation import official_billstatus_rollcall_source as BILLSTATUS

try:
    from legislation_measure_key import CANONICAL_KEY_RE
except ImportError:  # pragma: no cover - package import path
    from fec.legislation_measure_key import CANONICAL_KEY_RE


SCHEMA_VERSION = "official-rollcall-staging-v1"
FIXTURE_SCHEMA = "official-rollcall-fixture-manifest-v1"
USER_AGENT = "Melange-legislation-audit/1.0 (+official roll-call verification)"
ACTION_SOURCE_CHOICES = ("auto", "billstatus", "congress_api")

# The production model's exact 18 scoring categories.  Query IDs and legacy
# labels are normalized into these labels; CU Tax Status is intentionally
# explicit rather than falling through an old 17-category table.
CATEGORIES = (
    "Examination & Supervision",
    "CU Capital Requirements",
    "NCUA Budget & Insurance",
    "Interchange",
    "Payments",
    "Technology & Emerging",
    "Fraud Prevention",
    "Data Privacy & Cyber",
    "Charter Modernization",
    "Defense CU Issues",
    "Field of Membership",
    "CDFI Fund",
    "CFPB",
    "Credit Card Mandates",
    "TCPA",
    "Member Business Lending",
    "Mortgage & Housing",
    "CU Tax Status",
)

CATEGORY_ALIASES = {
    "examination_supervision": "Examination & Supervision",
    "credit_union_capital_requirements": "CU Capital Requirements",
    "cu_capital_requirements": "CU Capital Requirements",
    "ncua_budget_share_insurance": "NCUA Budget & Insurance",
    "ncua_budget_insurance": "NCUA Budget & Insurance",
    "interchange": "Interchange",
    "payments": "Payments",
    "technology_emerging_issues": "Technology & Emerging",
    "technology_emerging": "Technology & Emerging",
    "fraud_prevention": "Fraud Prevention",
    "data_privacy_cybersecurity": "Data Privacy & Cyber",
    "data_privacy_cyber": "Data Privacy & Cyber",
    "charter_modernization_governance": "Charter Modernization",
    "charter_modernization": "Charter Modernization",
    "defense_credit_union_issues": "Defense CU Issues",
    "defense_cu_issues": "Defense CU Issues",
    "field_of_membership": "Field of Membership",
    "cdfi_fund": "CDFI Fund",
    "cfpb": "CFPB",
    "credit_card_mandates": "Credit Card Mandates",
    "credit_card_consumer_fee": "Credit Card Mandates",
    "tcpa": "TCPA",
    "telephone_consumer": "TCPA",
    "member_business_lending": "Member Business Lending",
    "mortgage_lending_housing": "Mortgage & Housing",
    "mortgage_housing": "Mortgage & Housing",
    "credit_union_tax_status": "CU Tax Status",
    "cu_tax_status": "CU Tax Status",
}
for _category in CATEGORIES:
    CATEGORY_ALIASES[_category.casefold()] = _category

MEASURE_TYPES = {
    "hr",
    "s",
    "hjres",
    "sjres",
    "hconres",
    "sconres",
    "hres",
    "sres",
    "hamdt",
    "samdt",
}
AMENDMENT_TYPES = {"hamdt", "samdt"}
ACCEPTED_DISPOSITIONS = {
    "accept",
    "accepted",
    "include",
    "included",
    "includedforscoring",
    "included_for_scoring",
}
BIOGUIDE_RE = re.compile(r"^[A-Z]\d{6}$")
# ``CANONICAL_KEY_RE`` is imported above.  It used to be
# ``^\d{1,3}:[a-z]+:\d+$`` here, which accepted keys every downstream gate
# rejects.  Long-form measure names ("houseresolution") are still normalized by
# ``canonical_measure_type`` when they arrive in a *row field*; only the
# canonical key *string* is strict.


class CollectorError(RuntimeError):
    """A fail-closed collector contract violation."""


@dataclasses.dataclass(frozen=True, order=True)
class MeasureKey:
    congress: int
    measure_type: str
    number: int

    @property
    def canonical(self) -> str:
        return f"{self.congress}:{self.measure_type}:{self.number}"

    @property
    def record_kind(self) -> str:
        return "amendment" if self.measure_type in AMENDMENT_TYPES else "bill"

    def to_dict(self) -> dict[str, Any]:
        return {
            "congress": self.congress,
            "measure_type": self.measure_type,
            "number": self.number,
            "canonical_key": self.canonical,
        }


@dataclasses.dataclass(frozen=True, order=True)
class EventKey:
    congress: int
    chamber: str
    session: int
    roll: int

    @property
    def canonical(self) -> str:
        return f"{self.congress}:{self.chamber.casefold()}:{self.session}:{self.roll}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "congress": self.congress,
            "chamber": self.chamber,
            "session": self.session,
            "roll": self.roll,
            "event_id": self.canonical,
        }


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_token(value: Any) -> str:
    token = normalize_space(value).casefold()
    return re.sub(r"[^a-z0-9]+", "_", token).strip("_")


def clean_url(url: str) -> str:
    """Remove secrets and normalize a URL used as an immutable source identity."""
    parts = urllib.parse.urlsplit(str(url))
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key.casefold() != "api_key"]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
    )


def parse_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def canonical_measure_type(value: Any) -> str | None:
    token = re.sub(r"[^a-z]", "", str(value or "").casefold())
    aliases = {
        "hr": "hr",
        "housebill": "hr",
        "s": "s",
        "senatebill": "s",
        "hjres": "hjres",
        "housejointresolution": "hjres",
        "sjres": "sjres",
        "senatejointresolution": "sjres",
        "hconres": "hconres",
        "houseconcurrentresolution": "hconres",
        "sconres": "sconres",
        "senateconcurrentresolution": "sconres",
        "hres": "hres",
        "houseresolution": "hres",
        "sres": "sres",
        "senateresolution": "sres",
        "hamdt": "hamdt",
        "houseamendment": "hamdt",
        "samdt": "samdt",
        "senateamendment": "samdt",
    }
    return aliases.get(token)


def parse_canonical_measure_key(value: Any) -> MeasureKey:
    match = CANONICAL_KEY_RE.fullmatch(str(value or "").strip().casefold())
    if not match:
        raise CollectorError(f"invalid canonical measure key: {value!r}")
    measure_type = canonical_measure_type(match.group("measure_type"))
    if measure_type not in MEASURE_TYPES:
        raise CollectorError(f"unsupported measure type in canonical key: {value!r}")
    congress = int(match.group("congress"))
    number = int(match.group("number"))
    if congress <= 0 or number <= 0:
        raise CollectorError(f"non-positive canonical measure component: {value!r}")
    return MeasureKey(congress, measure_type, number)


def measure_key_from_row(row: dict[str, Any]) -> MeasureKey:
    if row.get("canonical_key"):
        return parse_canonical_measure_key(row["canonical_key"])
    congress = parse_int(row.get("congress") or row.get("Congress"))
    measure_type = canonical_measure_type(
        row.get("measure_type") or row.get("type") or row.get("Type")
    )
    number = parse_int(row.get("number") or row.get("measure_number") or row.get("Number"))
    if congress is None or measure_type is None or number is None:
        raise CollectorError(f"accepted row lacks a canonical measure identity: {row!r}")
    return MeasureKey(congress, measure_type, number)


def canonical_category(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = normalize_space(value)
    return CATEGORY_ALIASES.get(text.casefold()) or CATEGORY_ALIASES.get(normalize_token(text))


def category_from_row(row: dict[str, Any]) -> str:
    candidates = (
        row.get("category_label"),
        row.get("category"),
        row.get("Category"),
        row.get("scoring_category_name"),
        row.get("query_id"),
    )
    for value in candidates:
        category = canonical_category(value)
        if category:
            return category
    raise CollectorError(
        "accepted row has no recognized scoring category (all 18 are required): "
        + json.dumps(row, ensure_ascii=False)[:500]
    )


def normalize_direction(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if float(value) > 0:
            return 1
        if float(value) < 0:
            return -1
        return 0
    token = normalize_token(value)
    positive = {
        "1",
        "pro",
        "support",
        "yea_is_pro",
        "yes_is_pro",
        "expand",
        "advance",
        "cu_support",
    }
    negative = {
        "1_negative",  # defensive only; "-1" is handled before tokenization
        "anti",
        "oppose",
        "nay_is_pro",
        "no_is_pro",
        "restrain",
        "block",
        "cu_oppose",
    }
    if str(value).strip() == "-1" or token in negative:
        return -1
    if token in positive:
        return 1
    if token in {"0", "neutral", "no_direction", "unscored"}:
        return 0
    return None


def _direction_from_row(
    row: dict[str, Any], source_path: Path, line_number: int
) -> dict[str, Any] | None:
    raw = None
    for field in (
        "direction",
        "direction_sign",
        "cu_direction",
        "pro_industry_sign",
        "aligned_yea_sign",
    ):
        if field in row and row[field] not in (None, ""):
            raw = row[field]
            break
    if raw is None:
        return None
    sign = normalize_direction(raw)
    if sign is None:
        raise CollectorError(
            f"invalid direction {raw!r} at {source_path}:{line_number}"
        )
    provenance = row.get("direction_provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    source_url = clean_url(
        str(
            provenance.get("source_url")
            or row.get("direction_source_url")
            or row.get("official_direction_source_url")
            or ""
        )
    )
    source_reference = normalize_space(
        provenance.get("source_reference")
        or row.get("direction_source_reference")
        or row.get("direction_source")
    )
    rationale = normalize_space(
        provenance.get("rationale")
        or provenance.get("reason")
        or row.get("direction_reason")
        or row.get("direction_rationale")
    )
    return {
        "sign": sign,
        "meaning": (
            "yea_is_industry_aligned"
            if sign == 1
            else "nay_is_industry_aligned"
            if sign == -1
            else "neutral_unscored"
        ),
        "source_url": source_url or None,
        "source_reference": source_reference or None,
        "rationale": rationale or None,
        "input_path": str(source_path),
        "input_line": line_number,
        "provenance_complete": bool((source_url or source_reference) and rationale),
    }


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise CollectorError(f"input file does not exist: {path}")
    suffix = path.suffix.casefold()
    if suffix in {".ndjson", ".jsonl"}:
        rows = []
        with path.open(encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CollectorError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
                if not isinstance(row, dict):
                    raise CollectorError(f"non-object record at {path}:{line_number}")
                row = dict(row)
                row["_input_line"] = line_number
                rows.append(row)
        return rows
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise CollectorError(f"invalid JSON in {path}: {exc}") from exc
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = (
                payload.get("records")
                or payload.get("measures")
                or payload.get("directions")
                or payload.get("members")
            )
            if rows is None:
                rows = [payload]
        else:
            raise CollectorError(f"unsupported JSON root in {path}")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise CollectorError(f"JSON record collection is invalid in {path}")
        return [dict(row, _input_line=index) for index, row in enumerate(rows, 1)]
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return [
                dict(row, _input_line=index)
                for index, row in enumerate(csv.DictReader(handle), 2)
            ]
    raise CollectorError(f"unsupported input format for {path}; use JSON, NDJSON, or CSV")


def load_accepted_measures(
    accepted_path: Path, directions_path: Path | None = None
) -> tuple[dict[MeasureKey, dict[str, Any]], list[dict[str, Any]]]:
    rows = read_records(accepted_path)
    if not rows:
        raise CollectorError("accepted measure ledger is empty")
    measures: dict[MeasureKey, dict[str, Any]] = {}
    for row in rows:
        membership_status = row.get("membership_status")
        if membership_status is not None:
            normalized_membership_status = normalize_token(membership_status)
            if normalized_membership_status not in {"included", "excluded"}:
                raise CollectorError(
                    f"invalid membership_status at "
                    f"{accepted_path}:{row['_input_line']}: "
                    f"{membership_status!r}"
                )
            if normalized_membership_status == "excluded":
                continue
        if row.get("scoring_category") is False:
            continue
        disposition = row.get("disposition")
        if disposition is not None and normalize_token(disposition) not in ACCEPTED_DISPOSITIONS:
            raise CollectorError(
                f"non-accepted row supplied to accepted ledger at "
                f"{accepted_path}:{row['_input_line']}: {disposition!r}"
            )
        included_flag = row.get("included_for_scoring")
        if included_flag is False:
            raise CollectorError(
                f"included_for_scoring=false in accepted ledger at "
                f"{accepted_path}:{row['_input_line']}"
            )
        key = measure_key_from_row(row)
        category = category_from_row(row)
        measure = measures.setdefault(
            key,
            {
                "measure_key": key,
                "categories": {},
                "source_rows": [],
                "congress_gov_url": clean_url(
                    str(row.get("congress_gov_url") or row.get("canonical_url") or "")
                )
                or None,
                "title": normalize_space(row.get("title")) or None,
            },
        )
        measure["source_rows"].append(row["_input_line"])
        entry = measure["categories"].setdefault(
            category, {"direction_candidates": [], "source_rows": []}
        )
        entry["source_rows"].append(row["_input_line"])
        decision = _direction_from_row(row, accepted_path, row["_input_line"])
        if decision:
            entry["direction_candidates"].append(decision)

    if not measures:
        raise CollectorError(
            "accepted measure ledger has no included scoring measure/category edges"
        )

    if directions_path:
        for row in read_records(directions_path):
            key = measure_key_from_row(row)
            if key not in measures:
                raise CollectorError(
                    f"direction row references a measure absent from the accepted ledger: "
                    f"{key.canonical}"
                )
            category = category_from_row(row)
            if category not in measures[key]["categories"]:
                raise CollectorError(
                    f"direction row references an unaccepted measure/category edge: "
                    f"{key.canonical}/{category}"
                )
            decision = _direction_from_row(
                row, directions_path, int(row["_input_line"])
            )
            if not decision:
                raise CollectorError(
                    f"direction row lacks a direction at "
                    f"{directions_path}:{row['_input_line']}"
                )
            measures[key]["categories"][category]["direction_candidates"].append(decision)

    direction_audit = []
    for key, measure in sorted(measures.items()):
        for category, entry in sorted(measure["categories"].items()):
            candidates = entry.pop("direction_candidates")
            signatures = {
                (
                    candidate["sign"],
                    candidate["source_url"],
                    candidate["source_reference"],
                    candidate["rationale"],
                )
                for candidate in candidates
            }
            signs = {candidate["sign"] for candidate in candidates}
            complete = all(candidate["provenance_complete"] for candidate in candidates)
            if not candidates:
                status, reason, decision = (
                    "ambiguous",
                    "missing_explicit_category_direction",
                    None,
                )
            elif len(signs) != 1:
                status, reason, decision = (
                    "ambiguous",
                    "conflicting_category_directions",
                    None,
                )
            elif not complete:
                status, reason, decision = (
                    "ambiguous",
                    "direction_provenance_incomplete",
                    None,
                )
            else:
                # Equivalent duplicate evidence is retained in candidates, while
                # the scoring decision is one deterministic normalized object.
                decision = sorted(
                    candidates,
                    key=lambda item: (
                        item["sign"],
                        item["source_url"] or "",
                        item["source_reference"] or "",
                        item["rationale"] or "",
                    ),
                )[0]
                status, reason = "included", "explicit_direction_with_provenance"
            entry["direction"] = decision
            entry["direction_status"] = status
            entry["direction_reason"] = reason
            direction_audit.append(
                {
                    "schema": SCHEMA_VERSION,
                    **key.to_dict(),
                    "category": category,
                    "disposition": status,
                    "reason": reason,
                    "candidate_count": len(candidates),
                    "distinct_candidate_count": len(signatures),
                    "candidates": candidates,
                    "selected_direction": decision,
                }
            )
    return measures, direction_audit


class MemberResolver:
    """Resolve official member identifiers to Bioguide without fuzzy matching."""

    def __init__(
        self,
        rows: Iterable[dict[str, Any]] = (),
        *,
        supplemental_map_present: bool = False,
    ):
        self.supplemental_map_present = supplemental_map_present
        self.by_identifier: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
        self.by_name_state: dict[tuple[str, int | None, str, str], set[str]] = (
            collections.defaultdict(set)
        )
        self.provenance: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for row in rows:
            bioguide = normalize_space(
                row.get("bioguide_id") or row.get("bioguide") or row.get("Bioguide")
            ).upper()
            if not BIOGUIDE_RE.fullmatch(bioguide):
                raise CollectorError(f"invalid Bioguide ID in member map: {bioguide!r}")
            for field in (
                "lis_id",
                "lis_member_id",
                "house_id",
                "official_id",
                "name_id",
            ):
                value = normalize_space(row.get(field))
                if value:
                    self.by_identifier[(field, value.casefold())].add(bioguide)
            chamber = normalize_chamber(row.get("chamber"), allow_none=True)
            congress = parse_int(row.get("congress"))
            name = normalize_member_name(row.get("member_name") or row.get("name"))
            state = normalize_space(row.get("state")).upper()
            if chamber and name and state:
                self.by_name_state[(chamber, congress, name, state)].add(bioguide)
            self.provenance[bioguide].append(
                {
                    "source_url": clean_url(str(row.get("source_url") or "")) or None,
                    "source_reference": normalize_space(row.get("source_reference")) or None,
                    "input_line": row.get("_input_line"),
                }
            )

    @classmethod
    def from_path(cls, path: Path | None) -> "MemberResolver":
        return cls(
            read_records(path) if path else (),
            supplemental_map_present=path is not None,
        )

    def resolve(
        self,
        *,
        chamber: str,
        congress: int,
        direct_bioguide: Any = None,
        identifiers: dict[str, Any] | None = None,
        name: Any = None,
        state: Any = None,
    ) -> dict[str, Any]:
        direct = normalize_space(direct_bioguide).upper()
        if direct:
            if BIOGUIDE_RE.fullmatch(direct):
                return {
                    "status": "included",
                    "bioguide_id": direct,
                    "method": "official_xml_bioguide",
                    "identity_source": "official_xml_bioguide",
                    "candidates": [direct],
                }
            return {
                "status": "unmatched",
                "bioguide_id": None,
                "method": "invalid_official_xml_bioguide",
                "identity_source": "official_xml_bioguide",
                "candidates": [],
            }
        candidates: set[str] = set()
        methods = []
        for field, value in (identifiers or {}).items():
            token = normalize_space(value)
            if not token:
                continue
            found = self.by_identifier.get((field, token.casefold()), set())
            if found:
                candidates.update(found)
                methods.append(field)
        if not candidates:
            normalized_name = normalize_member_name(name)
            normalized_state = normalize_space(state).upper()
            for lookup_congress in (congress, None):
                found = self.by_name_state.get(
                    (chamber, lookup_congress, normalized_name, normalized_state), set()
                )
                if found:
                    candidates.update(found)
                    methods.append("exact_name_state")
        ordered = sorted(candidates)
        if len(ordered) == 1:
            return {
                "status": "included",
                "bioguide_id": ordered[0],
                "method": "+".join(sorted(set(methods))),
                "identity_source": "supplemental_member_map",
                "candidates": ordered,
                "mapping_provenance": self.provenance.get(ordered[0], []),
            }
        return {
            "status": "ambiguous" if ordered else "unmatched",
            "bioguide_id": None,
            "method": "member_map",
            "identity_source": (
                "supplemental_member_map"
                if self.supplemental_map_present
                else "none"
            ),
            "candidates": ordered,
        }


def normalize_member_name(value: Any) -> str:
    text = normalize_space(value).casefold()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return normalize_space(text)


def normalize_chamber(value: Any, allow_none: bool = False) -> str | None:
    token = normalize_token(value)
    if token in {"h", "house", "house_of_representatives", "u_s_house"}:
        return "House"
    if token in {"s", "senate", "u_s_senate"}:
        return "Senate"
    if allow_none and not token:
        return None
    raise CollectorError(f"invalid chamber: {value!r}")


class FixtureFetcher:
    """Read URL-addressed responses from an offline fixture manifest.

    Manifest example::

        {
          "schema": "official-rollcall-fixture-manifest-v1",
          "responses": {
            "https://api.congress.gov/v3/bill/119/hr/1/actions?format=json&limit=250":
              {"path": "actions.json", "status": 200,
               "content_type": "application/json"},
            "https://clerk.house.gov/evs/2025/roll001.xml":
              {"path": "roll001.xml", "status": 200,
               "content_type": "application/xml"}
          }
        }
    """

    def __init__(self, manifest_path: Path):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CollectorError(f"invalid fixture manifest {manifest_path}: {exc}") from exc
        if manifest.get("schema") != FIXTURE_SCHEMA:
            raise CollectorError(
                f"fixture manifest schema must be {FIXTURE_SCHEMA!r}"
            )
        responses = manifest.get("responses")
        if not isinstance(responses, dict):
            raise CollectorError("fixture manifest responses must be an object")
        self.base = manifest_path.parent
        self.responses = {clean_url(key): value for key, value in responses.items()}

    def fetch(self, url: str) -> dict[str, Any]:
        source_url = clean_url(url)
        spec = self.responses.get(source_url)
        if not isinstance(spec, dict):
            raise CollectorError(f"fixture has no response for {source_url}")
        status = int(spec.get("status", 200))
        if status != 200:
            raise CollectorError(f"fixture returned HTTP {status} for {source_url}")
        path = (self.base / str(spec.get("path") or "")).resolve()
        try:
            path.relative_to(self.base.resolve())
        except ValueError as exc:
            raise CollectorError(f"fixture path escapes fixture directory: {path}") from exc
        if not path.is_file():
            raise CollectorError(f"fixture response file does not exist: {path}")
        return {
            "source_url": source_url,
            "final_url": source_url,
            "status": status,
            "content_type": str(spec.get("content_type") or "application/octet-stream"),
            "content": path.read_bytes(),
        }


class HttpFetcher:
    def __init__(self, congress_api_key: str | None = None, timeout: int = 45):
        self.api_key = congress_api_key or ""
        self.timeout = timeout

    def fetch(self, url: str) -> dict[str, Any]:
        source_url = clean_url(url)
        parts = urllib.parse.urlsplit(source_url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if parts.hostname and parts.hostname.casefold() == "api.congress.gov":
            if not self.api_key:
                raise CollectorError(
                    "Congress.gov API action source requires CONGRESS_API_KEY; "
                    "official BILLSTATUS is available only for bills/resolutions"
                )
            query.append(("api_key", self.api_key))
        request_url = urllib.parse.urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urllib.parse.urlencode(query),
                parts.fragment,
            )
        )
        request = urllib.request.Request(
            request_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content = response.read()
                status = int(response.status)
                final_url = clean_url(response.geturl())
                content_type = response.headers.get_content_type()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CollectorError(f"official source fetch failed for {source_url}: {exc}") from exc
        if status != 200:
            raise CollectorError(f"official source returned HTTP {status}: {source_url}")
        # validate_official_vote_url pins clerk.house.gov / *.senate.gov BEFORE the
        # call, but urlopen follows redirects, so the body actually hashed into the
        # snapshot could come from anywhere. The sibling collectors
        # (collect_govinfo_bill_xml_versions.network_fetcher,
        # run_billstatus_bulk_enrichment.network_fetcher) both assert this; assert it
        # here too rather than recording final_url and never comparing it.
        if final_url != source_url:
            raise CollectorError(
                f"official source redirected: {source_url} -> {final_url}"
            )
        return {
            "source_url": source_url,
            "final_url": final_url,
            "status": status,
            "content_type": content_type,
            "content": content,
        }


class SnapshotStore:
    def __init__(self, root: Path):
        self.root = root
        self.raw = root / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.rows: list[dict[str, Any]] = []

    def record(self, response: dict[str, Any], source_kind: str) -> dict[str, Any]:
        content = response["content"]
        digest = sha256_bytes(content)
        content_type = str(response.get("content_type") or "")
        extension = (
            ".json"
            if "json" in content_type
            else ".xml"
            if "xml" in content_type
            else ".bin"
        )
        relative = Path("raw") / f"{digest}{extension}"
        target = self.root / relative
        if not target.exists():
            target.write_bytes(content)
        row = {
            "schema": SCHEMA_VERSION,
            "source_kind": source_kind,
            "source_url": clean_url(response["source_url"]),
            "final_url": clean_url(response.get("final_url") or response["source_url"]),
            "http_status": response.get("status"),
            "content_type": content_type,
            "content_sha256": digest,
            "content_size": len(content),
            "snapshot_path": relative.as_posix(),
        }
        if row not in self.rows:
            self.rows.append(row)
        return row


def actions_endpoint(key: MeasureKey) -> str:
    endpoint = "amendment" if key.record_kind == "amendment" else "bill"
    return (
        f"https://api.congress.gov/v3/{endpoint}/{key.congress}/"
        f"{key.measure_type}/{key.number}/actions?format=json&limit=250"
    )


def _as_list(value: Any, child_names: tuple[str, ...] = ()) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for child in child_names + ("item", "items"):
            if child in value:
                return _as_list(value[child])
        return [value]
    return []


def fetch_actions(
    key: MeasureKey, fetcher: Any, snapshots: SnapshotStore
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    url = actions_endpoint(key)
    actions: list[dict[str, Any]] = []
    sources = []
    seen = set()
    for _page in range(100):
        source_url = clean_url(url)
        if source_url in seen:
            raise CollectorError(f"Congress.gov actions pagination loop for {key.canonical}")
        seen.add(source_url)
        response = fetcher.fetch(source_url)
        sources.append(snapshots.record(response, "congress_gov_actions"))
        try:
            payload = json.loads(response["content"].decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectorError(
                f"Congress.gov actions response is not valid JSON for {key.canonical}: {exc}"
            ) from exc
        page_actions = payload.get("actions")
        if isinstance(page_actions, dict):
            page_actions = page_actions.get("item") or page_actions.get("items")
        if not isinstance(page_actions, list):
            raise CollectorError(
                f"Congress.gov actions response lacks an actions list for {key.canonical}"
            )
        if not all(isinstance(action, dict) for action in page_actions):
            raise CollectorError(f"invalid action item for {key.canonical}")
        actions.extend(page_actions)
        pagination = payload.get("pagination") or {}
        next_url = pagination.get("next") if isinstance(pagination, dict) else None
        if not next_url:
            break
        url = clean_url(str(next_url))
    else:
        raise CollectorError(f"Congress.gov actions pagination exceeded 100 pages: {key.canonical}")
    return actions, sources


def fetch_billstatus_actions(
    key: MeasureKey, fetcher: Any, snapshots: SnapshotStore
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch one complete official bulk record and bind it to ``key``."""
    try:
        url = BILLSTATUS.billstatus_url(
            key.congress, key.measure_type, key.number
        )
    except BILLSTATUS.BillStatusSourceError as exc:
        raise CollectorError(str(exc)) from exc
    response = fetcher.fetch(url)
    source = snapshots.record(response, "govinfo_congress_billstatus_actions")
    try:
        parsed = BILLSTATUS.parse_billstatus_actions(
            response["content"],
            congress=key.congress,
            measure_type=key.measure_type,
            number=key.number,
            source_url=response.get("final_url") or response["source_url"],
        )
    except BILLSTATUS.BillStatusSourceError as exc:
        raise CollectorError(str(exc)) from exc
    return parsed["actions"], [source]


def action_source_for_measure(
    key: MeasureKey,
    *,
    requested: str,
    fixture_manifest: Path | None,
    congress_api_key: str | None,
) -> str:
    """Choose one exhaustive official action source without silent inference."""
    if requested not in ACTION_SOURCE_CHOICES:
        raise CollectorError(f"invalid action source policy: {requested!r}")
    if requested != "auto":
        selected = requested
    elif fixture_manifest is not None:
        # Existing URL-addressed fixtures are API-shaped unless the caller
        # explicitly requests a BILLSTATUS fixture.
        selected = "congress_api"
    elif key.measure_type in BILLSTATUS.BILLSTATUS_SUPPORTED_TYPES:
        selected = "billstatus"
    elif congress_api_key:
        selected = "congress_api"
    else:
        raise CollectorError(
            "official amendment action source unavailable: BILLSTATUS does "
            f"not publish {key.measure_type}; provide CONGRESS_API_KEY or an "
            "exhaustive official Congress.gov fixture"
        )
    if (
        selected == "billstatus"
        and key.measure_type not in BILLSTATUS.BILLSTATUS_SUPPORTED_TYPES
    ):
        raise CollectorError(
            "BILLSTATUS action source is unsupported for amendment measure "
            f"{key.canonical}; provide official Congress.gov amendment actions"
        )
    if (
        selected == "congress_api"
        and fixture_manifest is None
        and not congress_api_key
    ):
        raise CollectorError(
            "Congress.gov API action source requires CONGRESS_API_KEY"
        )
    return selected


def fetch_measure_actions(
    key: MeasureKey,
    *,
    fetcher: Any,
    snapshots: SnapshotStore,
    requested: str,
    fixture_manifest: Path | None,
    congress_api_key: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    selected = action_source_for_measure(
        key,
        requested=requested,
        fixture_manifest=fixture_manifest,
        congress_api_key=congress_api_key,
    )
    if selected == "billstatus":
        actions, sources = fetch_billstatus_actions(key, fetcher, snapshots)
    else:
        actions, sources = fetch_actions(key, fetcher, snapshots)
    return actions, sources, selected


PROCEDURAL_PATTERNS = (
    r"\bcloture\b",
    r"\bmotion to proceed\b",
    r"\brecommit\b",
    r"\btable\b",
    r"\bprevious question\b",
    r"\bpoint of order\b",
    r"\bwaiv(?:e|ing)\b",
    r"\bmotion to adjourn\b",
    r"\bquorum\b",
    r"\bsustain(?:ing)? the ruling\b",
    r"\bordering the previous question\b",
)


def classify_final_passage(measure_type: str, text: Any) -> tuple[str | None, str]:
    normalized = normalize_space(text).casefold()
    if not normalized:
        return None, "missing_vote_question"
    if any(re.search(pattern, normalized) for pattern in PROCEDURAL_PATTERNS):
        return None, "procedural_vote"
    if measure_type in AMENDMENT_TYPES:
        if "amendment" in normalized and re.search(
            r"\b(agree|agreed|adopt|adopted|pass|passed)\b", normalized
        ):
            return "amendment_disposition", "final_amendment_disposition"
        return None, "not_final_amendment_disposition"
    if "amendment" in normalized and not (
        "concur" in normalized or "resolving differences" in normalized
    ):
        return None, "amendment_vote_on_non_amendment_measure"
    if "conference report" in normalized and re.search(
        r"\b(agree|agreed|adopt|adopted)\b", normalized
    ):
        return "conference_report", "final_conference_report_disposition"
    if re.search(r"\bconcur(?:red|ring)?\b", normalized) and re.search(
        r"\b(agree|agreed|pass|passed|concur|concurred)\b", normalized
    ):
        return "resolving_differences", "final_resolving_differences_disposition"
    if re.search(
        r"\b(on passage|passage of|passed house|passed senate|passed by|"
        r"motion to suspend the rules and pass|suspend the rules and pass)\b",
        normalized,
    ):
        return "passage", "final_passage"
    if measure_type in {"hres", "sres", "hconres", "sconres", "hjres", "sjres"}:
        if re.search(
            r"\b(on agreeing to the (?:joint |concurrent )?resolution|"
            r"resolution agreed to|agreed to in (?:the )?(?:house|senate))\b",
            normalized,
        ):
            return "resolution_adoption", "final_resolution_disposition"
    return None, "not_recognized_as_final_measure_disposition"


def recorded_votes_from_action(action: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        vote
        for vote in _as_list(
            action.get("recordedVotes") or action.get("recorded_votes"),
            ("recordedVote", "recorded_vote"),
        )
        if isinstance(vote, dict)
    ]


def recorded_vote_metadata(
    vote: dict[str, Any], fallback_congress: int
) -> tuple[EventKey | None, str | None, str | None]:
    try:
        chamber = normalize_chamber(vote.get("chamber"))
    except CollectorError:
        chamber = None
    congress = parse_int(vote.get("congress")) or fallback_congress
    session = parse_int(vote.get("sessionNumber") or vote.get("session"))
    roll = parse_int(vote.get("rollNumber") or vote.get("roll"))
    url = clean_url(str(vote.get("url") or ""))
    if not chamber or congress is None or session is None or roll is None:
        return None, url or None, "recorded_vote_metadata_incomplete"
    return EventKey(congress, chamber, session, roll), url or None, None


def validate_official_vote_url(chamber: str, url: str) -> str | None:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").casefold()
    if parts.scheme.casefold() != "https":
        return "official_vote_url_not_https"
    if chamber == "House" and host != "clerk.house.gov":
        return "house_vote_url_not_house_clerk"
    if chamber == "Senate" and not (host == "senate.gov" or host.endswith(".senate.gov")):
        return "senate_vote_url_not_senate_dot_gov"
    return None


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold().replace("-", "_")


def xml_text(root: ET.Element, *names: str) -> str | None:
    wanted = {normalize_token(name) for name in names}
    for node in root.iter():
        if normalize_token(local_name(node.tag)) in wanted:
            text = normalize_space(node.text)
            if text:
                return text
    return None


def xml_children(node: ET.Element, name: str) -> list[ET.Element]:
    wanted = normalize_token(name)
    return [
        child
        for child in node.iter()
        if normalize_token(local_name(child.tag)) == wanted
    ]


DISPLAY_MEASURE_PATTERNS = (
    (r"\bH\s*J\s*RES(?:OLUTION)?\s*(\d+)\b", "hjres"),
    (r"\bS\s*J\s*RES(?:OLUTION)?\s*(\d+)\b", "sjres"),
    (r"\bH\s*CON\s*RES(?:OLUTION)?\s*(\d+)\b", "hconres"),
    (r"\bS\s*CON\s*RES(?:OLUTION)?\s*(\d+)\b", "sconres"),
    (r"\bH\s*RES(?:OLUTION)?\s*(\d+)\b", "hres"),
    (r"\bS\s*RES(?:OLUTION)?\s*(\d+)\b", "sres"),
    (r"\bH\s*(?:AMDT|AMENDMENT)\s*(\d+)\b", "hamdt"),
    (r"\bS\s*(?:AMDT|AMENDMENT)\s*(\d+)\b", "samdt"),
    (r"\bH\s*R\s*(\d+)\b", "hr"),
    (r"\bS\s*(\d+)\b", "s"),
)


def parse_display_measure(value: Any, congress: int) -> list[MeasureKey]:
    text = normalize_space(value).upper().replace(".", " ")
    text = re.sub(r"\s+", " ", text)
    found = []
    for pattern, measure_type in DISPLAY_MEASURE_PATTERNS:
        for match in re.finditer(pattern, text):
            found.append(MeasureKey(congress, measure_type, int(match.group(1))))
    return sorted(set(found))


def measure_from_parts(
    congress: int, measure_type: Any, number: Any
) -> MeasureKey | None:
    canonical_type = canonical_measure_type(measure_type)
    canonical_number = parse_int(number)
    if canonical_type not in MEASURE_TYPES or canonical_number is None:
        return None
    return MeasureKey(congress, canonical_type, canonical_number)


def parse_house_vote(
    root: ET.Element, resolver: MemberResolver
) -> dict[str, Any]:
    congress = parse_int(xml_text(root, "congress"))
    session = parse_int(xml_text(root, "session"))
    roll = parse_int(xml_text(root, "rollcall-num", "rollcall_num", "vote_number"))
    if congress is None:
        raise CollectorError("House XML lacks Congress")
    question = xml_text(root, "vote-question", "vote_question", "question")
    result = xml_text(root, "vote-result", "vote_result", "result")
    legis_num = xml_text(root, "legis-num", "legis_num", "document")
    measures = parse_display_measure(legis_num, congress)
    votes = []
    for recorded in xml_children(root, "recorded_vote"):
        legislators = [
            node
            for node in recorded.iter()
            if normalize_token(local_name(node.tag)) == "legislator"
        ]
        if not legislators:
            continue
        legislator = legislators[0]
        attrs = {normalize_token(key): value for key, value in legislator.attrib.items()}
        direct = (
            attrs.get("bioguide_id")
            or attrs.get("bioguide")
            or attrs.get("name_id")
        )
        name = normalize_space(legislator.text)
        state = attrs.get("state")
        position = xml_text(recorded, "vote")
        resolution = resolver.resolve(
            chamber="House",
            congress=congress,
            direct_bioguide=direct,
            identifiers={
                "house_id": attrs.get("name_id"),
                "official_id": attrs.get("id"),
            },
            name=name,
            state=state,
        )
        votes.append(
            {
                "member_name": name or None,
                "state": state or None,
                "party": attrs.get("party"),
                "official_identifiers": {
                    key: value
                    for key, value in {
                        "name_id": attrs.get("name_id"),
                        "official_id": attrs.get("id"),
                    }.items()
                    if value
                },
                "position": normalize_space(position) or None,
                "bioguide_resolution": resolution,
            }
        )
    return {
        "event_key": (
            EventKey(congress, "House", session, roll)
            if session is not None and roll is not None
            else None
        ),
        "question": question,
        "result": result,
        "title": xml_text(root, "vote-desc", "vote_desc", "legislative_num"),
        "date": xml_text(root, "action-date", "action_date", "vote_date"),
        "measure_keys": measures,
        "context_measure_keys": measures,
        "votes": votes,
    }


def parse_senate_vote(
    root: ET.Element, resolver: MemberResolver
) -> dict[str, Any]:
    congress = parse_int(xml_text(root, "congress"))
    session = parse_int(xml_text(root, "session"))
    roll = parse_int(xml_text(root, "vote_number", "rollcall_num"))
    if congress is None:
        raise CollectorError("Senate XML lacks Congress")
    question = xml_text(root, "question")
    title = xml_text(root, "vote_title", "title")
    result = xml_text(root, "vote_result", "result")
    document_type = xml_text(root, "document_type")
    document_number = xml_text(root, "document_number")
    document_key = measure_from_parts(congress, document_type, document_number)
    context = [document_key] if document_key else []
    amendment_candidates = []
    for field in (
        "amendment_number",
        "amendment_to_amendment_number",
        "amendment",
    ):
        amendment_candidates.extend(parse_display_measure(xml_text(root, field), congress))
    combined_question = " ".join(filter(None, (question, title)))
    amendment_candidates.extend(parse_display_measure(combined_question, congress))
    amendment_candidates = [
        key for key in sorted(set(amendment_candidates)) if key.measure_type in AMENDMENT_TYPES
    ]
    if "amendment" in normalize_space(combined_question).casefold() and amendment_candidates:
        measures = amendment_candidates
    else:
        measures = context
    votes = []
    for member in xml_children(root, "member"):
        name = xml_text(member, "member_full", "member_name", "full_name")
        if not name:
            first = xml_text(member, "first_name") or ""
            last = xml_text(member, "last_name") or ""
            name = normalize_space(f"{first} {last}")
        state = xml_text(member, "state")
        lis_id = xml_text(member, "lis_member_id")
        direct = xml_text(member, "bioguide_id", "bioguide")
        position = xml_text(member, "vote_cast", "vote")
        resolution = resolver.resolve(
            chamber="Senate",
            congress=congress,
            direct_bioguide=direct,
            identifiers={"lis_member_id": lis_id, "lis_id": lis_id},
            name=name,
            state=state,
        )
        votes.append(
            {
                "member_name": name or None,
                "state": state,
                "party": xml_text(member, "party"),
                "official_identifiers": {"lis_member_id": lis_id} if lis_id else {},
                "position": normalize_space(position) or None,
                "bioguide_resolution": resolution,
            }
        )
    return {
        "event_key": (
            EventKey(congress, "Senate", session, roll)
            if session is not None and roll is not None
            else None
        ),
        "question": question,
        "result": result,
        "title": title,
        "date": xml_text(root, "vote_date"),
        "measure_keys": measures,
        "context_measure_keys": context,
        "votes": votes,
    }


def parse_official_vote_xml(
    content: bytes, chamber: str, resolver: MemberResolver
) -> dict[str, Any]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise CollectorError(f"official {chamber} vote source is not valid XML: {exc}") from exc
    return (
        parse_house_vote(root, resolver)
        if chamber == "House"
        else parse_senate_vote(root, resolver)
    )


def serialize_official_vote(official: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            value.to_dict()
            if isinstance(value, EventKey)
            else [item.to_dict() for item in value]
            if key in {"measure_keys", "context_measure_keys"}
            else value
        )
        for key, value in official.items()
        if key != "votes"
    }


def normalized_vote_value(position: Any) -> int | None:
    token = normalize_token(position)
    if token in {"yea", "yes", "aye"}:
        return 1
    if token in {"nay", "no"}:
        return -1
    return None


def provisional_event_id(
    key: MeasureKey, action: dict[str, Any], vote: dict[str, Any]
) -> str:
    payload = json.dumps(
        {"measure": key.canonical, "action": action, "vote": vote},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return "ambiguous:" + sha256_bytes(payload)[:20]


def event_candidate(
    *,
    key: MeasureKey,
    measure: dict[str, Any],
    action: dict[str, Any],
    vote: dict[str, Any],
    action_sources: list[dict[str, Any]],
    fetcher: Any,
    snapshots: SnapshotStore,
    resolver: MemberResolver,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    event_key, vote_url, metadata_error = recorded_vote_metadata(vote, key.congress)
    action_text = normalize_space(action.get("text"))
    action_class, action_class_reason = classify_final_passage(key.measure_type, action_text)
    event_id = event_key.canonical if event_key else provisional_event_id(key, action, vote)
    base = {
        "schema": SCHEMA_VERSION,
        "event_id": event_id,
        "event_key": event_key.to_dict() if event_key else None,
        "measure_key": key.to_dict(),
        "categories": sorted(measure["categories"]),
        "congress_action": {
            "date": action.get("actionDate"),
            "time": action.get("actionTime"),
            "text": action_text or None,
            "type": action.get("type"),
            "action_code": action.get("actionCode"),
            "source_system": action.get("sourceSystem"),
            "recorded_vote": vote,
        },
        "action_source_snapshots": action_sources,
        "official_vote_url": vote_url,
        "official_vote_snapshot": None,
        "final_passage_class": action_class,
        "official_event": None,
        "disposition": None,
        "reason": None,
        "aggregation_ready": False,
        "category_directions": [],
    }
    blockers: list[str] = []
    if metadata_error:
        base.update(disposition="ambiguous", reason=metadata_error)
        blockers.append(f"{event_id}:{metadata_error}")
        return base, [], blockers
    assert event_key is not None
    if event_key.congress != key.congress:
        base.update(disposition="unmatched", reason="event_congress_differs_from_measure")
        blockers.append(f"{event_id}:event_congress_differs_from_measure")
        return base, [], blockers
    if not action_class:
        base.update(disposition="excluded", reason=action_class_reason)
        return base, [], blockers
    if not vote_url:
        base.update(disposition="ambiguous", reason="final_action_lacks_official_vote_url")
        blockers.append(f"{event_id}:final_action_lacks_official_vote_url")
        return base, [], blockers
    url_error = validate_official_vote_url(event_key.chamber, vote_url)
    if url_error:
        base.update(disposition="ambiguous", reason=url_error)
        blockers.append(f"{event_id}:{url_error}")
        return base, [], blockers
    try:
        response = fetcher.fetch(vote_url)
        vote_snapshot = snapshots.record(response, "official_chamber_rollcall")
        official = parse_official_vote_xml(
            response["content"], event_key.chamber, resolver
        )
    except CollectorError as exc:
        base.update(
            disposition="ambiguous",
            reason="official_vote_fetch_or_parse_failed",
            error=str(exc),
        )
        blockers.append(f"{event_id}:official_vote_fetch_or_parse_failed")
        return base, [], blockers
    base["official_vote_snapshot"] = vote_snapshot
    base["official_event"] = serialize_official_vote(official)
    official_key = official["event_key"]
    if official_key is None:
        base.update(disposition="ambiguous", reason="official_xml_event_key_incomplete")
        blockers.append(f"{event_id}:official_xml_event_key_incomplete")
        return base, official["votes"], blockers
    if official_key != event_key:
        base.update(disposition="unmatched", reason="official_xml_event_key_mismatch")
        blockers.append(f"{event_id}:official_xml_event_key_mismatch")
        return base, official["votes"], blockers
    official_classes = [
        classify_final_passage(
            key.measure_type,
            " ".join(
                filter(
                    None,
                    (
                        official.get("question"),
                        official.get("title"),
                        official.get("result"),
                    ),
                )
            ),
        )
    ]
    official_class, official_reason = official_classes[0]
    if not official_class:
        base.update(
            disposition="excluded",
            reason="official_vote_not_final_measure_disposition",
            official_classification_reason=official_reason,
        )
        return base, official["votes"], blockers
    measure_keys = set(official["measure_keys"])
    if key not in measure_keys:
        base.update(
            disposition="unmatched",
            reason="official_xml_measure_identity_mismatch",
            official_measure_keys=[item.to_dict() for item in sorted(measure_keys)],
        )
        blockers.append(f"{event_id}:official_xml_measure_identity_mismatch")
        return base, official["votes"], blockers
    if len(measure_keys) != 1:
        base.update(
            disposition="ambiguous",
            reason="official_xml_measure_identity_not_unique",
            official_measure_keys=[item.to_dict() for item in sorted(measure_keys)],
        )
        blockers.append(f"{event_id}:official_xml_measure_identity_not_unique")
        return base, official["votes"], blockers
    if not official["votes"]:
        base.update(disposition="ambiguous", reason="official_xml_has_no_member_votes")
        blockers.append(f"{event_id}:official_xml_has_no_member_votes")
        return base, [], blockers
    base.update(
        disposition="included",
        reason="official_final_disposition_exact_measure_match",
        official_final_passage_class=official_class,
    )
    return base, official["votes"], blockers


def merge_duplicate_event_candidates(
    candidates: list[tuple[dict[str, Any], list[dict[str, Any]]]]
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    grouped: dict[tuple[str, str], list[tuple[dict[str, Any], list[dict[str, Any]]]]] = (
        collections.defaultdict(list)
    )
    for event, votes in candidates:
        grouped[(event["event_id"], event["measure_key"]["canonical_key"])].append(
            (event, votes)
        )
    merged = []
    for _identity, rows in sorted(grouped.items()):
        first_event, first_votes = rows[0]
        if len(rows) > 1:
            first_event = dict(first_event)
            first_event["duplicate_congress_action_count"] = len(rows)
            first_event["duplicate_action_sources"] = [
                row[0]["congress_action"] for row in rows
            ]
        merged.append((first_event, first_votes))
    return merged


def reconcile_cross_measure_event_collisions(
    events_and_votes: list[tuple[dict[str, Any], list[dict[str, Any]]]]
) -> list[str]:
    by_event: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for event, _votes in events_and_votes:
        if event["disposition"] == "included" and not event["event_id"].startswith("ambiguous:"):
            by_event[event["event_id"]].append(event)
    blockers = []
    for event_id, events in sorted(by_event.items()):
        measures = {event["measure_key"]["canonical_key"] for event in events}
        if len(measures) <= 1:
            continue
        blockers.append(f"{event_id}:event_matches_multiple_accepted_measures")
        for event in events:
            event["disposition"] = "ambiguous"
            event["reason"] = "event_matches_multiple_accepted_measures"
            event["candidate_measure_keys"] = sorted(measures)
    return blockers


def apply_directions_and_aggregate(
    events_and_votes: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    measures: dict[MeasureKey, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    member_rows = []
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    blockers: list[str] = []
    for event, votes in events_and_votes:
        key = parse_canonical_measure_key(event["measure_key"]["canonical_key"])
        category_directions = []
        for category, edge in sorted(measures[key]["categories"].items()):
            direction = edge["direction"]
            row = {
                "category": category,
                "disposition": edge["direction_status"],
                "reason": edge["direction_reason"],
                "direction": direction,
            }
            category_directions.append(row)
            if event["disposition"] == "included" and edge["direction_status"] != "included":
                blockers.append(
                    f"{event['event_id']}:{key.canonical}:{category}:"
                    f"{edge['direction_reason']}"
                )
        event["category_directions"] = category_directions
        event["aggregation_ready"] = bool(
            event["disposition"] == "included"
            and category_directions
            and all(row["disposition"] == "included" for row in category_directions)
        )
        unresolved_members = 0
        included_votes = 0
        for index, vote in enumerate(votes, 1):
            resolution = vote["bioguide_resolution"]
            vote_value = normalized_vote_value(vote.get("position"))
            base = {
                "schema": SCHEMA_VERSION,
                "event_id": event["event_id"],
                "measure_key": key.to_dict(),
                "vote_row": index,
                "member_name": vote.get("member_name"),
                "state": vote.get("state"),
                "party": vote.get("party"),
                "official_identifiers": vote.get("official_identifiers") or {},
                "position": vote.get("position"),
                "bioguide_id": resolution.get("bioguide_id"),
                "bioguide_resolution": resolution,
                "category_contributions": [],
                "disposition": None,
                "reason": None,
            }
            if event["disposition"] != "included":
                base.update(
                    disposition="excluded",
                    reason="event_not_included",
                )
            elif resolution["status"] != "included":
                base.update(
                    disposition=resolution["status"],
                    reason="member_bioguide_not_uniquely_resolved",
                )
                unresolved_members += 1
            elif vote_value is None:
                base.update(
                    disposition="excluded",
                    reason="non_directional_vote_position",
                )
            else:
                contributions = []
                for direction_row in category_directions:
                    direction = direction_row["direction"]
                    if direction_row["disposition"] != "included" or direction is None:
                        contributions.append(
                            {
                                "category": direction_row["category"],
                                "disposition": "ambiguous",
                                "reason": direction_row["reason"],
                                "aligned_value": None,
                                "direction": direction,
                            }
                        )
                        continue
                    if direction["sign"] == 0:
                        contributions.append(
                            {
                                "category": direction_row["category"],
                                "disposition": "excluded",
                                "reason": "explicit_neutral_direction",
                                "aligned_value": None,
                                "direction": direction,
                            }
                        )
                        continue
                    aligned = vote_value * direction["sign"]
                    contribution = {
                        "category": direction_row["category"],
                        "disposition": "included",
                        "reason": "recorded_vote_with_explicit_direction",
                        "aligned_value": aligned,
                        "direction": direction,
                    }
                    contributions.append(contribution)
                    cell_key = (resolution["bioguide_id"], direction_row["category"])
                    cell = cells.setdefault(
                        cell_key,
                        {
                            "bioguide_id": resolution["bioguide_id"],
                            "category": direction_row["category"],
                            "aligned_sum": 0,
                            "recorded_vote_count": 0,
                            "event_ids": [],
                            "events": [],
                        },
                    )
                    cell["aligned_sum"] += aligned
                    cell["recorded_vote_count"] += 1
                    if event["event_id"] not in cell["event_ids"]:
                        cell["event_ids"].append(event["event_id"])
                    cell["events"].append(
                        {
                            "event_id": event["event_id"],
                            "measure_key": key.canonical,
                            "position": vote["position"],
                            "aligned_value": aligned,
                            "direction": direction,
                        }
                    )
                base["category_contributions"] = contributions
                if any(row["disposition"] == "included" for row in contributions):
                    base.update(
                        disposition="included",
                        reason="at_least_one_category_contribution_included",
                    )
                    included_votes += 1
                elif any(row["disposition"] == "ambiguous" for row in contributions):
                    base.update(
                        disposition="ambiguous",
                        reason="category_direction_not_aggregation_ready",
                    )
                else:
                    base.update(
                        disposition="excluded",
                        reason="no_scored_category_contribution",
                    )
            member_rows.append(base)
        event["member_vote_counts"] = {
            "official_rows": len(votes),
            "included_rows": included_votes,
            "unresolved_bioguide_rows": unresolved_members,
        }
        if event["disposition"] == "included" and unresolved_members:
            blockers.append(
                f"{event['event_id']}:unresolved_member_bioguide_rows={unresolved_members}"
            )

    cell_rows = []
    by_member: dict[str, dict[str, Any]] = collections.defaultdict(dict)
    for (_bioguide, _category), cell in sorted(cells.items()):
        cell["event_ids"].sort()
        cell["events"].sort(
            key=lambda row: (row["event_id"], row["measure_key"], row["position"])
        )
        cell["lean"] = cell["aligned_sum"] / cell["recorded_vote_count"]
        cell_rows.append(cell)
        by_member[cell["bioguide_id"]][cell["category"]] = {
            "lean": cell["lean"],
            "aligned_sum": cell["aligned_sum"],
            "recorded_vote_count": cell["recorded_vote_count"],
            "event_ids": cell["event_ids"],
            "events": cell["events"],
        }
    aggregate = {
        "schema": SCHEMA_VERSION,
        "authority": (
            "staged official roll-call aggregate; not production vote_category_lean.json"
        ),
        "categories": list(CATEGORIES),
        "members": {member: by_member[member] for member in sorted(by_member)},
        "cells": cell_rows,
    }
    return member_rows, aggregate, blockers


def member_identity_receipt(
    member_rows: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]],
    member_map_path: Path | None,
) -> dict[str, Any]:
    """Bind optional-map presence and exact identity outcomes for accepted events."""
    included_event_ids = {
        event["event_id"]
        for event in events
        if event.get("disposition") == "included"
    }
    rows = [
        row for row in member_rows if row.get("event_id") in included_event_ids
    ]
    status_counts = collections.Counter(
        str((row.get("bioguide_resolution") or {}).get("status") or "missing")
        for row in rows
    )
    source_counts = collections.Counter(
        str(
            (row.get("bioguide_resolution") or {}).get("identity_source")
            or "missing"
        )
        for row in rows
        if (row.get("bioguide_resolution") or {}).get("status") == "included"
    )
    unresolved = status_counts["unmatched"] + status_counts["ambiguous"]
    map_receipt = None
    if member_map_path is not None:
        resolved = member_map_path.resolve()
        map_receipt = {
            "path": str(resolved),
            "size": resolved.stat().st_size,
            "sha256": sha256_file(resolved),
        }
    return {
        "supplemental_member_map": {
            "status": "present" if member_map_path is not None else "absent",
            "present": member_map_path is not None,
            "receipt": map_receipt,
        },
        "accepted_event_member_row_count": len(rows),
        "resolution_status_counts": dict(sorted(status_counts.items())),
        "included_resolution_counts": {
            "official_xml_bioguide": source_counts[
                "official_xml_bioguide"
            ],
            "supplemental_member_map": source_counts[
                "supplemental_member_map"
            ],
        },
        "unmatched_member_row_count": status_counts["unmatched"],
        "ambiguous_member_row_count": status_counts["ambiguous"],
        "unresolved_or_ambiguous_member_row_count": unresolved,
        "all_accepted_event_members_uniquely_resolved": unresolved == 0,
        "fuzzy_matching_used": False,
    }


def build_measure_audit(
    measures: dict[MeasureKey, dict[str, Any]],
    action_results: dict[MeasureKey, dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_measure: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for event in events:
        by_measure[event["measure_key"]["canonical_key"]].append(event)
    rows = []
    for key, measure in sorted(measures.items()):
        result = action_results[key]
        measure_events = by_measure.get(key.canonical, [])
        counts = collections.Counter(event["disposition"] for event in measure_events)
        if result.get("error"):
            disposition, reason = "ambiguous", "official_actions_fetch_failed"
        elif counts["ambiguous"]:
            disposition, reason = "ambiguous", "one_or_more_event_matches_ambiguous"
        elif counts["unmatched"]:
            disposition, reason = "unmatched", "one_or_more_event_matches_unmatched"
        elif counts["included"]:
            disposition, reason = "included", "official_final_rollcall_included"
        else:
            disposition, reason = "excluded", "no_official_final_passage_rollcall"
        rows.append(
            {
                "schema": SCHEMA_VERSION,
                **key.to_dict(),
                "title": measure.get("title"),
                "categories": sorted(measure["categories"]),
                "accepted_source_rows": sorted(set(measure["source_rows"])),
                "actions_endpoint": actions_endpoint(key),
                "official_action_source": result.get("source"),
                "official_action_source_url": result.get("source_url"),
                "actions_fetched": not bool(result.get("error")),
                "action_count": result.get("action_count", 0),
                "recorded_vote_candidate_count": len(measure_events),
                "event_disposition_counts": dict(sorted(counts.items())),
                "event_ids": sorted(event["event_id"] for event in measure_events),
                "disposition": disposition,
                "reason": reason,
                "error": result.get("error"),
            }
        )
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8", newline='\r\n',
    )


def write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def assert_staging_output_path(path: Path, workspace_root: Path) -> Path:
    resolved = path.expanduser().resolve()
    root = workspace_root.resolve()
    if resolved == root:
        raise CollectorError("output may not be the workspace root")
    if not any(re.search(r"(audit|stag)", part, re.I) for part in resolved.parts):
        raise CollectorError(
            "output path must explicitly identify an audit/staging directory"
        )
    if resolved.exists():
        raise CollectorError(
            f"immutable staging output already exists; choose a new directory: {resolved}"
        )
    return resolved


def collect(
    *,
    accepted_path: Path,
    out_path: Path,
    workspace_root: Path,
    fixture_manifest: Path | None = None,
    directions_path: Path | None = None,
    member_map_path: Path | None = None,
    congress_api_key: str | None = None,
    action_source: str = "auto",
) -> dict[str, Any]:
    """Build one immutable staging generation and return its manifest."""
    out_path = assert_staging_output_path(out_path, workspace_root)
    measures, direction_audit = load_accepted_measures(
        accepted_path, directions_path
    )
    resolver = MemberResolver.from_path(member_map_path)
    fetcher = (
        FixtureFetcher(fixture_manifest)
        if fixture_manifest
        else HttpFetcher(congress_api_key)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(
        tempfile.mkdtemp(prefix=f".{out_path.name}.tmp-", dir=str(out_path.parent))
    )
    try:
        snapshots = SnapshotStore(temp)
        candidates: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        blockers: list[str] = []
        action_results: dict[MeasureKey, dict[str, Any]] = {}
        for key, measure in sorted(measures.items()):
            selected_source = None
            try:
                actions, action_sources, selected_source = fetch_measure_actions(
                    key,
                    fetcher=fetcher,
                    snapshots=snapshots,
                    requested=action_source,
                    fixture_manifest=fixture_manifest,
                    congress_api_key=congress_api_key,
                )
                action_results[key] = {
                    "action_count": len(actions),
                    "error": None,
                    "source": selected_source,
                    "source_url": (
                        action_sources[0]["source_url"]
                        if action_sources
                        else None
                    ),
                }
            except CollectorError as exc:
                action_results[key] = {
                    "action_count": 0,
                    "error": str(exc),
                    "source": selected_source,
                    "source_url": None,
                }
                blockers.append(f"{key.canonical}:official_actions_fetch_failed")
                continue
            for action in actions:
                for vote in recorded_votes_from_action(action):
                    event, member_votes, event_blockers = event_candidate(
                        key=key,
                        measure=measure,
                        action=action,
                        vote=vote,
                        action_sources=action_sources,
                        fetcher=fetcher,
                        snapshots=snapshots,
                        resolver=resolver,
                    )
                    candidates.append((event, member_votes))
                    blockers.extend(event_blockers)
        candidates = merge_duplicate_event_candidates(candidates)
        blockers.extend(reconcile_cross_measure_event_collisions(candidates))
        member_votes, aggregate, aggregate_blockers = apply_directions_and_aggregate(
            candidates, measures
        )
        blockers.extend(aggregate_blockers)
        events = sorted(
            (event for event, _votes in candidates),
            key=lambda row: (
                row["event_id"],
                row["measure_key"]["canonical_key"],
                row["disposition"],
            ),
        )
        member_votes.sort(
            key=lambda row: (
                row["event_id"],
                row.get("bioguide_id") or "",
                row.get("member_name") or "",
                row["vote_row"],
            )
        )
        measure_audit = build_measure_audit(measures, action_results, events)
        snapshot_rows = sorted(
            snapshots.rows,
            key=lambda row: (row["source_kind"], row["source_url"], row["content_sha256"]),
        )
        direction_audit.sort(
            key=lambda row: (row["canonical_key"], row["category"])
        )

        write_ndjson(temp / "official_rollcall_event_ledger.ndjson", events)
        write_ndjson(temp / "rollcall_measure_match_audit.ndjson", measure_audit)
        write_ndjson(temp / "official_rollcall_member_votes.ndjson", member_votes)
        write_ndjson(temp / "rollcall_direction_audit.ndjson", direction_audit)
        write_ndjson(temp / "source_snapshots.ndjson", snapshot_rows)
        write_json(temp / "official_rollcall_aggregate.json", aggregate)

        event_counts = collections.Counter(event["disposition"] for event in events)
        vote_counts = collections.Counter(row["disposition"] for row in member_votes)
        measure_counts = collections.Counter(row["disposition"] for row in measure_audit)
        direction_counts = collections.Counter(
            row["disposition"] for row in direction_audit
        )
        direction_reason_counts = collections.Counter(
            row["reason"] for row in direction_audit
        )
        action_source_counts = collections.Counter(
            str(result.get("source") or "unavailable")
            for result in action_results.values()
        )
        identity_receipt = member_identity_receipt(
            member_votes,
            events,
            member_map_path,
        )
        unique_blockers = sorted(set(blockers))
        input_paths = [accepted_path]
        if directions_path:
            input_paths.append(directions_path)
        if member_map_path:
            input_paths.append(member_map_path)
        if fixture_manifest:
            input_paths.append(fixture_manifest)
        output_names = (
            "official_rollcall_event_ledger.ndjson",
            "rollcall_measure_match_audit.ndjson",
            "official_rollcall_member_votes.ndjson",
            "rollcall_direction_audit.ndjson",
            "source_snapshots.ndjson",
            "official_rollcall_aggregate.json",
        )
        manifest = {
            "schema": SCHEMA_VERSION,
            "generated_at": utc_now(),
            "staging_only": True,
            "production_vote_category_lean_written": False,
            "authority_chain": [
                "accepted canonical measure/category ledger",
                (
                    "complete official Congress.gov API actions or exact-identity "
                    "GovInfo/Congress.gov BILLSTATUS bulk XML"
                ),
                "official recorded-vote association and immutable event key",
                "official House Clerk or Senate roll-call XML",
                "exact Bioguide resolution",
                "explicit category direction provenance",
            ],
            "official_action_source_policy": {
                "requested": action_source,
                "congress_api_key_present": bool(congress_api_key),
                "fixture_manifest_present": fixture_manifest is not None,
                "billstatus_no_key_supported_measure_types": sorted(
                    BILLSTATUS.BILLSTATUS_SUPPORTED_TYPES
                ),
                "billstatus_amendment_support": False,
                "amendment_fail_closed_requirement": (
                    "CONGRESS_API_KEY or exhaustive official Congress.gov "
                    "amendment-action fixture"
                ),
                "selected_source_counts": dict(
                    sorted(action_source_counts.items())
                ),
            },
            "immutable_measure_key": [
                "Congress",
                "measure_type",
                "measure_number",
            ],
            "immutable_event_key": [
                "Congress",
                "chamber",
                "session",
                "roll",
            ],
            "categories": list(CATEGORIES),
            "accepted_measure_count": len(measures),
            "accepted_measure_category_edge_count": sum(
                len(measure["categories"]) for measure in measures.values()
            ),
            "event_disposition_counts": dict(sorted(event_counts.items())),
            "member_vote_disposition_counts": dict(sorted(vote_counts.items())),
            "member_identity_resolution": identity_receipt,
            "measure_disposition_counts": dict(sorted(measure_counts.items())),
            "direction_disposition_counts": dict(sorted(direction_counts.items())),
            # These remain separate hard failures.  In particular, an old
            # CFPB:Reduce/CFPB:Expand subtype is never silently mapped unless a
            # staged type-aware direction row preserves its sign and evidence.
            "direction_reason_counts": dict(sorted(direction_reason_counts.items())),
            "aggregate_member_count": len(aggregate["members"]),
            "aggregate_cell_count": len(aggregate["cells"]),
            "blocking_issue_count": len(unique_blockers),
            "blocking_issues": unique_blockers,
            "acceptance_passed": not unique_blockers,
            "implementation_sources": [
                {
                    "path": str(path.resolve()),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in (
                    Path(__file__),
                    Path(BILLSTATUS.__file__),
                )
            ],
            "inputs": [
                {
                    "path": str(path.resolve()),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in input_paths
            ],
            "outputs": {
                name: {
                    "path": name,
                    "size": (temp / name).stat().st_size,
                    "sha256": sha256_file(temp / name),
                }
                for name in output_names
            },
        }
        write_json(temp / "manifest.json", manifest)
        for attempt in range(8):
            try:
                os.replace(temp, out_path)
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
            "Stage official, type-aware final-passage roll-call evidence. "
            "Never writes production vote_category_lean.json."
        )
    )
    result.add_argument(
        "--accepted",
        required=True,
        type=Path,
        help="accepted canonical measure/category ledger (JSON, NDJSON, or CSV)",
    )
    result.add_argument(
        "--out",
        required=True,
        type=Path,
        help="new immutable audit/staging output directory",
    )
    result.add_argument(
        "--directions",
        type=Path,
        help="optional explicit measure/category direction ledger",
    )
    result.add_argument(
        "--member-map",
        type=Path,
        help="optional official-ID/LIS-to-Bioguide mapping ledger",
    )
    result.add_argument(
        "--fixture-manifest",
        type=Path,
        help="offline URL-addressed official-source fixture manifest",
    )
    result.add_argument(
        "--action-source",
        choices=ACTION_SOURCE_CHOICES,
        default="auto",
        help=(
            "official action discovery source; auto uses BILLSTATUS for "
            "bills/resolutions and requires Congress API/fixture for amendments"
        ),
    )
    result.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent.parent,
        help=argparse.SUPPRESS,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = collect(
            accepted_path=args.accepted,
            out_path=args.out,
            workspace_root=args.workspace_root,
            fixture_manifest=args.fixture_manifest,
            directions_path=args.directions,
            member_map_path=args.member_map,
            congress_api_key=os.environ.get("CONGRESS_API_KEY"),
            action_source=args.action_source,
        )
    except CollectorError as exc:
        print(f"collect_official_rollcall_events: FAIL CLOSED: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(args.out.resolve()),
                "accepted_measures": manifest["accepted_measure_count"],
                "events": manifest["event_disposition_counts"],
                "aggregate_cells": manifest["aggregate_cell_count"],
                "blocking_issues": manifest["blocking_issue_count"],
                "acceptance_passed": manifest["acceptance_passed"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if manifest["acceptance_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
