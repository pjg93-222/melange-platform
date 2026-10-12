# -*- coding: utf-8 -*-
"""Promote adjudicated employment evidence and build shared-affiliation edges.

This is the post-quarantine stage.  Source-native LDA identity is accepted, Senate
observations are split into bounded episodes, and external review is not a gate.
Covered-position parser and container-normalization gates remain fail closed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .employment_history import (
    duration_weight,
    intersect_ranges,
    range_days,
    union_ranges,
)
from .publication import _validate_record_semantics
from .schema_validation import validate_instance
from .source_manifest import canonical_json_bytes, sha256_file


SCHEMA_DIRECTORY = Path(__file__).resolve().parent / "schemas"
EPISODE_SCHEMA = json.loads(
    (SCHEMA_DIRECTORY / "employment_episode.schema.json").read_text(encoding="utf-8")
)
METHOD_VERSION = "employment-promotion-and-affiliation-v1"
REMOVED_GATES = (
    "IDENTITY_PRECISION_RECALL_GOLD_200_REQUIRED",
    "SENATE_TEMPORAL_OBSERVATION_AUDIT_REQUIRED",
    "FABLE_OPUS_5_ADVERSARIAL_PASS_REQUIRED",
)
RETAINED_GATES = (
    "COVERED_POSITION_PARSER_PRECISION_GOLD_100_REQUIRED",
    "CONTAINER_ADJUDICATION_REQUIRED",
)
WORD = re.compile(r"[a-z0-9]+")
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
GENERIC_SENATE = {"united states senate", "us senate", "u s senate"}
GENERIC_HOUSE = {
    "united states house of representatives", "us house of representatives",
    "u s house of representatives", "us house", "u s house",
}

# Owner ruling 2026-09-07 (CO_TENURE_EXCLUDED_CONTAINER_KINDS = {'admin'}): a
# chamber-wide payroll header says two people were paid by the same institution,
# not that they shared a workplace, so no person-person pair is enumerated inside
# one. These are the two chamber-wide buckets this module mints itself when an
# employer string normalises to the whole chamber; the affiliation GROUP is still
# emitted, only the clique over it is not.
CO_TENURE_EXCLUDED_CONTAINER_IDS = frozenset({
    "organization:united-states-senate",
    "organization:united-states-house-of-representatives",
})


class PromotionError(RuntimeError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _support_digest(values: Iterable[str]) -> str:
    return hashlib.sha256("\0".join(sorted(set(values))).encode("utf-8")).hexdigest()


def _rows(path: str | Path) -> Iterator[dict[str, Any]]:
    source = Path(path)
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _write_gzip(path: Path, values: Iterable[Mapping[str, Any]], *, key: str) -> int:
    rows = sorted(values, key=lambda row: str(row[key]))
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            for row in rows:
                compressed.write(canonical_json_bytes(row))
    return len(rows)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_bytes(canonical_json_bytes(value))


def _norm(value: Any) -> str:
    return " ".join(WORD.findall(str(value or "").casefold()))


def _slug(value: str) -> str:
    words = WORD.findall(value.casefold())
    text = "-".join(words[:12]) or "unknown"
    return f"{text}:{hashlib.sha256(value.casefold().encode('utf-8')).hexdigest()[:16]}"


def _clean_bioname(value: str) -> tuple[str, str] | None:
    if "," not in value:
        return None
    last, given = value.split(",", 1)
    last_words = [word for word in WORD.findall(last.casefold()) if word not in SUFFIXES]
    given_words = [word for word in WORD.findall(given.casefold()) if word not in SUFFIXES]
    if not last_words or not given_words:
        return None
    return " ".join(given_words), " ".join(last_words)


class MemberDirectory:
    """Conservative full-name resolver backed by the local Voteview spine."""

    def __init__(self, voteview_csv: str | Path):
        aliases: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
        with Path(voteview_csv).open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                bioguide = (row.get("bioguide_id") or "").strip()
                parsed = _clean_bioname(row.get("bioname") or "")
                if not bioguide or parsed is None:
                    continue
                given, last = parsed
                aliases[(given, last)].add(bioguide)
        self.aliases = {
            key: next(iter(values)) for key, values in aliases.items() if len(values) == 1
        }

    def resolve(self, employer: str) -> str | None:
        normalized = _norm(employer)
        tokens = set(normalized.split())
        if not tokens.intersection(
            {"senator", "sen", "representative", "rep", "congressman", "congresswoman", "hon"}
        ):
            return None
        matches: list[tuple[int, str]] = []
        for (given, last), bioguide in self.aliases.items():
            given_tokens = given.split()
            last_tokens = last.split()
            if not set(last_tokens).issubset(tokens):
                continue
            if given_tokens[0] not in tokens:
                continue
            score = len(given_tokens) + len(last_tokens)
            matches.append((score, bioguide))
        if not matches:
            return None
        best = max(score for score, _ in matches)
        ids = {bioguide for score, bioguide in matches if score == best}
        return next(iter(ids)) if len(ids) == 1 else None


def _registrant_aliases(value: str) -> set[str]:
    normalized = _norm(value)
    aliases = {normalized}
    for marker in (" dba ", " doing business as ", " formerly "):
        if marker in f" {normalized} ":
            left, right = normalized.split(marker.strip(), 1)
            aliases.update({_norm(left), _norm(right)})
    return {alias for alias in aliases if len(alias) >= 5}


class ContainerDirectory:
    def __init__(
        self,
        members: MemberDirectory,
        registrant_rows: Sequence[Mapping[str, Any]],
    ):
        self.members = members
        by_person: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in registrant_rows:
            item = dict(row)
            item["aliases"] = _registrant_aliases(str(row.get("employer_name") or ""))
            by_person[str(row["lda_source_person_id"])].append(item)
        self.registrants = by_person

    def resolve(self, person_id: str, employer: str) -> dict[str, Any] | None:
        raw = " ".join(str(employer or "").split())
        normalized = _norm(raw)
        if not normalized:
            return None
        registrant_matches = []
        for row in self.registrants.get(person_id, []):
            if any(
                normalized == alias
                or (len(normalized) >= 8 and normalized in alias)
                or (len(alias) >= 8 and alias in normalized)
                for alias in row["aliases"]
            ):
                registrant_matches.append(row)
        registrant_ids = {
            str(row["registrant_source_org_id"]) for row in registrant_matches
        }
        if len(registrant_ids) == 1:
            organization_id = next(iter(registrant_ids))
            return {
                "normalized_container_id": organization_id,
                "container_kind": "registrant",
                "member_bioguide_id": None,
                "organization_id": organization_id,
                "container_label": raw,
                "normalization_method": "PERSON_BOUND_LDA_REGISTRANT_ALIAS",
            }
        bioguide = self.members.resolve(raw)
        if bioguide:
            return {
                "normalized_container_id": f"member-office:{bioguide}",
                "container_kind": "member_office",
                "member_bioguide_id": bioguide,
                "organization_id": None,
                "container_label": raw,
                "normalization_method": "VOTEVIEW_FULL_NAME",
            }
        if normalized in GENERIC_SENATE:
            stable = "organization:united-states-senate"
        elif normalized in GENERIC_HOUSE:
            stable = "organization:united-states-house-of-representatives"
        else:
            stable = "organization:" + _slug(raw)
        return {
            "normalized_container_id": stable,
            "container_kind": "other_org",
            "member_bioguide_id": None,
            "organization_id": stable,
            "container_label": raw,
            "normalization_method": "NORMALIZED_LITERAL",
        }


def _decision_id(kind: str, subject: str) -> str:
    return f"employment:{kind}-decision:" + _digest({"kind": kind, "subject": subject})


def _episode(
    *, person_id: str, container: Mapping[str, Any], title: str | None,
    start_earliest: str | None, start_latest: str | None,
    end_earliest: str | None, end_latest: str | None,
    start_precision: str, end_precision: str, asserted_current: bool,
    source_as_of: str, support_ids: Iterable[str], source_lane: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    support = sorted(set(str(value) for value in support_ids if value))
    if not support:
        raise PromotionError("canonical episode has no support")
    core = {
        "person": person_id,
        "container": container["normalized_container_id"],
        "title": _norm(title),
        "start": [start_earliest, start_latest],
        "end": [end_earliest, end_latest],
        "asserted_current": asserted_current,
    }
    episode_id = "employment:episode:" + _digest(core)
    identity_id = _decision_id("identity", person_id)
    container_id = _decision_id("container", str(container["normalized_container_id"]))
    row = {
        "schema_id": "staffer-network-employment-episode-v1",
        "episode_id": episode_id,
        "resolved_person_id": person_id,
        "normalized_container_id": container["normalized_container_id"],
        "container_kind": container["container_kind"],
        "member_bioguide_id": container["member_bioguide_id"],
        "organization_id": container["organization_id"],
        "title": title,
        "start_earliest": start_earliest,
        "start_latest": start_latest,
        "end_earliest": end_earliest,
        "end_latest": end_latest,
        "start_precision": start_precision,
        "end_precision": end_precision,
        "asserted_current": asserted_current,
        "source_as_of": source_as_of,
        "identity_decision_id": identity_id,
        "identity_status": "ACCEPTED",
        "container_decision_id": container_id,
        "container_status": "ACCEPTED",
        "support_observation_ids": support,
        "support_digest": _support_digest(support),
        "episode_status": "ACTIVE",
    }
    decision = {
        "decision_id": "promotion:decision:" + _digest({
            "episode": episode_id, "lane": source_lane, "support": support,
        }),
        "episode_id": episode_id,
        "lda_source_person_id": person_id,
        "source_lane": source_lane,
        "identity_gate": "REMOVED_SOURCE_NATIVE_ID_ACCEPTED",
        "container_gate": "PASS",
        "container_normalization_method": container["normalization_method"],
        "covered_position_parser_gate": (
            "PASS_UPSTREAM" if source_lane == "lda_covered_position_parse" else "NOT_APPLICABLE"
        ),
        "senate_temporal_audit_gate": "REMOVED_SPLIT_TO_OBSERVATIONS",
        "external_review_gate": "REMOVED",
        "promotion_status": "PROMOTED",
    }
    return row, decision


def _month_bounds(raw: str, *, boundary: str) -> tuple[str, str]:
    year, month = (int(part) for part in raw.split("-"))
    first = date(year, month, 1)
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    last = next_month - timedelta(days=1)
    if boundary == "start":
        return first.isoformat(), last.isoformat()
    return (first + timedelta(days=1)).isoformat(), next_month.isoformat()


def _date_bounds(raw: str, *, boundary: str) -> tuple[str, str, str]:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        value = date.fromisoformat(raw)
        if boundary == "end":
            value += timedelta(days=1)
        return value.isoformat(), value.isoformat(), "day"
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        low, high = _month_bounds(raw, boundary=boundary)
        return low, high, "month"
    if re.fullmatch(r"\d{4}", raw):
        year = int(raw)
        if boundary == "start":
            return f"{year:04d}-01-01", f"{year:04d}-12-31", "year"
        return f"{year:04d}-01-02", f"{year + 1:04d}-01-01", "year"
    raise PromotionError(f"unsupported date literal: {raw}")


def _merge_exact(episodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in episodes:
        key = (
            row["resolved_person_id"], row["normalized_container_id"], _norm(row["title"]),
            row["start_earliest"], row["start_latest"], row["end_earliest"],
            row["end_latest"], row["asserted_current"],
        )
        prior = merged.get(key)
        if prior is None:
            merged[key] = row
            continue
        support = sorted(set(prior["support_observation_ids"] + row["support_observation_ids"]))
        prior["support_observation_ids"] = support
        prior["support_digest"] = _support_digest(support)
    return list(merged.values())


def _range(row: Mapping[str, Any]) -> tuple[date, date] | None:
    start = row.get("start_latest")
    end = row.get("end_earliest")
    if not start or not end:
        return None
    low, high = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    return (low, high) if high > low else None


def _overlaps(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    a, b = _range(left), _range(right)
    return bool(a and b and min(a[1], b[1]) > max(a[0], b[0]))


def _inherit_generic_container(
    person_id: str, employer: str, title: str, bounds: Mapping[str, Any],
    episodes: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    if _norm(employer) not in GENERIC_SENATE | GENERIC_HOUSE:
        return None
    candidates = {
        (
            row["normalized_container_id"], row["container_kind"],
            row["member_bioguide_id"], row["organization_id"],
        )
        for row in episodes
        if row["resolved_person_id"] == person_id
        and _norm(row.get("title")) == _norm(title)
        and _overlaps(row, bounds)
    }
    if len(candidates) != 1:
        return None
    container_id, kind, member, organization = next(iter(candidates))
    return {
        "normalized_container_id": container_id,
        "container_kind": kind,
        "member_bioguide_id": member,
        "organization_id": organization,
        "container_label": employer,
        "normalization_method": "TEMPORAL_ROLE_CORROBORATION",
    }


def _covered_support_map(
    path: str | Path, containers: ContainerDirectory,
) -> dict[tuple[str, str, str], list[str]]:
    """Index parser-retained LDA literals as corroboration, not standalone episodes."""
    support: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in _rows(path):
        person_id = str(row["lda_source_person_id"])
        claim_id = str(row["claim_id"])
        titles = [
            str(item.get("canonical_title") or item.get("text") or "")
            for item in row.get("role_title_mentions") or []
        ]
        offices = [
            str(item.get("text") or "")
            for item in row.get("office_employer_mentions") or []
        ]
        for office in offices:
            container = containers.resolve(person_id, office)
            if container is None:
                continue
            for title in titles:
                if title.strip():
                    support[(person_id, _norm(title), str(container["normalized_container_id"]))].add(claim_id)
    return {key: sorted(values) for key, values in support.items()}


def _affiliation_memberships(
    episodes: Sequence[Mapping[str, Any]],
    registrants: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for episode in episodes:
        rows.append({
            "membership_id": "affiliation:membership:" + _digest({"episode": episode["episode_id"]}),
            "person_id": episode["resolved_person_id"],
            "container_id": episode["normalized_container_id"],
            "container_kind": episode["container_kind"],
            "relationship_type": "EMPLOYMENT",
            "start_earliest": episode["start_earliest"],
            "start_latest": episode["start_latest"],
            "end_earliest": episode["end_earliest"],
            "end_latest": episode["end_latest"],
            "support_ids": episode["support_observation_ids"],
        })
    for row in registrants:
        observed_years = sorted(set(
            int(year) for year in (row.get("filing_years") or [])
        ))
        if not observed_years:
            observed_years = list(range(int(row["start_year"]), int(row["end_year"]) + 1))
        for year in observed_years:
            start = f"{year:04d}-01-01"
            end = f"{year + 1:04d}-01-01"
            support = [str(row["episode_id"]), f"lda-filing-year:{year}"]
            rows.append({
                "membership_id": "affiliation:membership:" + _digest({
                    "registrant": row["episode_id"], "filing_year": year,
                }),
                "person_id": str(row["lda_source_person_id"]),
                "container_id": str(row["registrant_source_org_id"]),
                "container_kind": "registrant",
                "relationship_type": "LDA_REGISTRANT_FILINGS",
                "start_earliest": start,
                "start_latest": start,
                "end_earliest": end,
                "end_latest": end,
                "support_ids": support,
            })
    return rows


def _groups_and_edges(
    memberships: Sequence[Mapping[str, Any]], *, tau_days: float,
    stats: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # D-019 receipt counters, measured AT the rejection site below rather than derived afterward
    # by subtraction (a subtraction cannot prove the overlap test fired -- it only restates
    # len(edges)). `stats` is optional and keyword-only so every existing caller, including the
    # test suite's direct `_groups_and_edges(memberships, tau_days=...)` calls, is unaffected; the
    # 2-tuple return here is unchanged.
    if stats is not None:
        stats["co_tenure_pairs_rejected_zero_overlap"] = 0
        # No container is ever excluded from pairing at this stage today. Reserved so a future
        # container-exclusion gate (STAFFA-8, Paul-gated, not part of this patch) has somewhere
        # real to report into instead of silently folding its skips into the zero-overlap count
        # above -- which VERIFY_STAFFA.md flagged as actively misleading if the two ever merge.
        stats["co_tenure_pairs_skipped_excluded_container"] = 0
    by_container: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in memberships:
        by_container[str(row["container_id"])].append(row)
    groups, edges = [], []
    for container, rows in sorted(by_container.items()):
        people = sorted({str(row["person_id"]) for row in rows})
        groups.append({
            "group_id": "affiliation:group:" + _digest({"container": container}),
            "container_id": container,
            "container_kinds": sorted({str(row["container_kind"]) for row in rows}),
            "relationship_types": sorted({str(row["relationship_type"]) for row in rows}),
            "person_ids": people,
            "person_count": len(people),
            "membership_count": len(rows),
        })
        if container in CO_TENURE_EXCLUDED_CONTAINER_IDS:
            if stats is not None:
                n = len(people)
                stats["co_tenure_pairs_skipped_excluded_container"] += n * (n - 1) // 2
            continue
        person_ranges: defaultdict[str, list[tuple[date, date]]] = defaultdict(list)
        person_support: defaultdict[str, set[str]] = defaultdict(set)
        person_relationships: defaultdict[str, set[str]] = defaultdict(set)
        for row in rows:
            interval = _range(row)
            if interval:
                person_ranges[str(row["person_id"])].append(interval)
            person_support[str(row["person_id"])].update(row["support_ids"])
            person_relationships[str(row["person_id"])].add(str(row["relationship_type"]))
        person_calendar = {
            person: union_ranges(ranges) for person, ranges in person_ranges.items()
        }
        for index, left in enumerate(people):
            left_calendar = person_calendar.get(left, [])
            for right in people[index + 1:]:
                overlap = intersect_ranges(
                    left_calendar, person_calendar.get(right, [])
                )
                days = range_days(overlap)
                if days <= 0:
                    if stats is not None:
                        stats["co_tenure_pairs_rejected_zero_overlap"] += 1
                    continue
                support = sorted(person_support[left] | person_support[right])
                edge_core = {"left": left, "right": right, "container": container}
                edges.append({
                    "edge_id": "affiliation:edge:" + _digest(edge_core),
                    "subject_id": left,
                    "object_id": right,
                    "container_id": container,
                    "relationship_types": sorted(
                        person_relationships[left] | person_relationships[right]
                    ),
                    "overlap_days": days,
                    "edge_weight": duration_weight(days, tau_days=tau_days),
                    "support_ids": support,
                    "support_digest": _support_digest(support),
                })
    return groups, edges


def promote_and_group(
    *, provisional_episodes: str | Path, senate_payroll: str | Path,
    authenticated_profiles: str | Path, registrant_associations: str | Path,
    covered_positions: str | Path,
    voteview_members: str | Path, output_root: str | Path,
    as_of: str, tau_days: float = 365.2425,
) -> dict[str, Any]:
    if not math.isfinite(tau_days) or tau_days <= 0:
        raise PromotionError("tau_days must be positive")
    output = Path(output_root)
    if output.exists() and any(output.iterdir()):
        raise PromotionError(f"output root is nonempty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    as_of_date = date.fromisoformat(as_of)
    registrants = list(_rows(registrant_associations))
    containers = ContainerDirectory(MemberDirectory(voteview_members), registrants)
    covered_support = _covered_support_map(covered_positions, containers)
    episodes: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for source in _rows(provisional_episodes):
        container = containers.resolve(
            str(source["lda_source_person_id"]), str(source.get("employer_candidate") or source.get("raw_employer"))
        )
        if container is None:
            blocked.append({"source_id": source["episode_id"], "gate": "CONTAINER_ADJUDICATION_REQUIRED"})
            continue
        row, decision = _episode(
            person_id=str(source["lda_source_person_id"]), container=container,
            title=source.get("title_candidate") or source.get("raw_title"),
            start_earliest=source.get("valid_start_earliest"),
            start_latest=source.get("valid_start_latest"),
            end_earliest=source.get("valid_end_earliest"),
            end_latest=source.get("valid_end_latest"),
            start_precision=str(source.get("start_precision") or "unknown"),
            end_precision=str(source.get("end_precision") or "unknown"),
            asserted_current=bool(source.get("asserted_current")), source_as_of=as_of,
            support_ids=source.get("support_candidate_ids") or source.get("source_candidate_ids") or [source["episode_id"]],
            source_lane="v3_provisional:" + "+".join(source.get("evidence_lanes") or []),
        )
        episodes.append(row); decisions.append(decision)

    for source in _rows(senate_payroll):
        unique = (
            source.get("queue_candidate_count_for_name") == 1
            and source.get("queue_display_name_count_for_name") == 1
        )
        if not unique:
            blocked.append({"source_id": source["match_candidate_id"], "gate": "SOURCE_NATIVE_ID_NOT_UNIQUE"})
            continue
        container = containers.resolve(
            str(source["lda_source_person_id"]), str(source.get("employing_office_candidate"))
        )
        if container is None:
            blocked.append({"source_id": source["match_candidate_id"], "gate": "CONTAINER_ADJUDICATION_REQUIRED"})
            continue
        for ordinal, interval in enumerate(source.get("observed_report_intervals") or [], start=1):
            if not interval.get("start") or not interval.get("end") or interval.get("basis") == "COMPLEX_QUALIFIER_REVIEW_REQUIRED":
                blocked.append({"source_id": source["match_candidate_id"], "gate": "INVALID_INTERVAL"})
                continue
            start = date.fromisoformat(str(interval["start"]))
            end = date.fromisoformat(str(interval["end"])) + timedelta(days=1)
            if end <= start or end > as_of_date + timedelta(days=1):
                blocked.append({"source_id": source["match_candidate_id"], "gate": "INVALID_INTERVAL"})
                continue
            support = [str(source["match_candidate_id"]), f"senate-interval:{ordinal}"]
            support.extend(covered_support.get((
                str(source["lda_source_person_id"]),
                _norm(source.get("title_candidate")),
                str(container["normalized_container_id"]),
            ), []))
            row, decision = _episode(
                person_id=str(source["lda_source_person_id"]), container=container,
                title=str(source.get("title_candidate") or "Senate staff"),
                start_earliest=start.isoformat(), start_latest=start.isoformat(),
                end_earliest=end.isoformat(), end_latest=end.isoformat(),
                start_precision="day", end_precision="day", asserted_current=False,
                source_as_of=as_of, support_ids=support, source_lane="senate_payroll_split",
            )
            episodes.append(row); decisions.append(decision)

    pre_profile = _merge_exact(episodes)
    education: list[dict[str, Any]] = []
    for profile in _rows(authenticated_profiles):
        person_id = str(profile["lda_source_person_id"])
        for ordinal, fact in enumerate(profile.get("candidate_facts") or [], start=1):
            if fact.get("fact_type") == "EDUCATION":
                education.append({
                    "education_id": "education:record:" + _digest({"person": person_id, "ordinal": ordinal, "fact": fact}),
                    "person_id": person_id,
                    "institution": fact.get("institution_raw"),
                    "degree": fact.get("degree_raw"),
                    "field": fact.get("field_raw"),
                    "raw_start_date": fact.get("raw_start_date"),
                    "raw_end_date": fact.get("raw_end_date"),
                    "source_urls": fact.get("source_urls") or [],
                })
                continue
            if fact.get("fact_type") != "EMPLOYMENT" or not fact.get("raw_start_date"):
                continue
            start_low, start_high, start_precision = _date_bounds(str(fact["raw_start_date"]), boundary="start")
            if fact.get("raw_end_date"):
                end_low, end_high, end_precision = _date_bounds(str(fact["raw_end_date"]), boundary="end")
                asserted_current = False
            else:
                end = as_of_date + timedelta(days=1)
                end_low = end_high = end.isoformat(); end_precision = "day"; asserted_current = True
            bounds = {
                "start_latest": start_high, "end_earliest": end_low,
            }
            container = _inherit_generic_container(
                person_id, str(fact.get("employer_raw")), str(fact.get("title_raw")), bounds, pre_profile
            ) or containers.resolve(person_id, str(fact.get("employer_raw")))
            if container is None:
                blocked.append({"source_id": f"authenticated-profile:{person_id}:{ordinal}", "gate": "CONTAINER_ADJUDICATION_REQUIRED"})
                continue
            support = ["authenticated-profile-fact:" + _digest({"person": person_id, "ordinal": ordinal, "fact": fact})]
            support.extend(covered_support.get((
                person_id, _norm(fact.get("title_raw")),
                str(container["normalized_container_id"]),
            ), []))
            row, decision = _episode(
                person_id=person_id, container=container, title=fact.get("title_raw"),
                start_earliest=start_low, start_latest=start_high,
                end_earliest=end_low, end_latest=end_high,
                start_precision=start_precision, end_precision=end_precision,
                asserted_current=asserted_current, source_as_of=as_of,
                support_ids=support, source_lane="authenticated_profile",
            )
            episodes.append(row); decisions.append(decision)

    episodes = _merge_exact(episodes)
    for row in episodes:
        validate_instance(row, EPISODE_SCHEMA)
        _validate_record_semantics(row, row["schema_id"])

    memberships = _affiliation_memberships(episodes, registrants)
    co_tenure_stats: dict[str, int] = {}
    groups, edges = _groups_and_edges(memberships, tau_days=tau_days, stats=co_tenure_stats)
    episode_path = output / "canonical_employment_episodes.ndjson.gz"
    decision_path = output / "employment_promotion_decisions.ndjson.gz"
    education_path = output / "canonical_education_records.ndjson.gz"
    membership_path = output / "service_affiliation_memberships.ndjson.gz"
    group_path = output / "service_affiliation_groups.ndjson.gz"
    edge_path = output / "shared_affiliation_edges.ndjson.gz"
    counts = {
        "canonical_employment_episodes": _write_gzip(episode_path, episodes, key="episode_id"),
        "promotion_decisions": _write_gzip(decision_path, decisions, key="decision_id"),
        "education_records": _write_gzip(education_path, education, key="education_id"),
        "affiliation_memberships": _write_gzip(membership_path, memberships, key="membership_id"),
        "affiliation_groups": _write_gzip(group_path, groups, key="group_id"),
        "shared_affiliation_edges": _write_gzip(edge_path, edges, key="edge_id"),
        "blocked_records": len(blocked),
        # D-019: every build must report how many same-container pairs were rejected for zero
        # overlap. Measured at the rejection site inside _groups_and_edges (not derived by
        # subtracting len(edges) from a candidate-pair count, which cannot prove the overlap test
        # actually fired). A zero here means the overlap test is not firing, not that every
        # co-located pair actually overlapped.
        "co_tenure_pairs_rejected_zero_overlap": co_tenure_stats["co_tenure_pairs_rejected_zero_overlap"],
        # Reserved for a future container-exclusion gate (STAFFA-8, Paul-gated, not part of this
        # patch): stays 0 today because _groups_and_edges excludes no container from pairing.
        "co_tenure_pairs_skipped_excluded_container": co_tenure_stats["co_tenure_pairs_skipped_excluded_container"],
    }
    people = {row["resolved_person_id"] for row in episodes}
    receipt = {
        "schema_id": "staffer-network-employment-promotion-receipt-v1",
        "status": "PASS",
        "method_version": METHOD_VERSION,
        "as_of": as_of,
        "quarantine_enabled": False,
        "removed_gates": list(REMOVED_GATES),
        "retained_gates": list(RETAINED_GATES),
        "canonical_identity_policy": "LDA_SOURCE_NATIVE_ID_ACCEPTED",
        "canonical_people": len(people),
        "counts": counts,
        "files": [
            {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in (episode_path, decision_path, education_path, membership_path, group_path, edge_path)
        ],
        "blocked_gate_counts": {
            gate: sum(row["gate"] == gate for row in blocked)
            for gate in sorted({row["gate"] for row in blocked})
        },
        "tau_days": tau_days,
        "edge_weight_formula": "1-exp(-overlap_days/tau_days)",
    }
    _write_json(output / "employment_promotion_receipt.json", receipt)
    _write_gzip(output / "employment_promotion_blocked.ndjson.gz", blocked, key="source_id")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provisional-episodes", required=True)
    parser.add_argument("--senate-payroll", required=True)
    parser.add_argument("--authenticated-profiles", required=True)
    parser.add_argument("--registrant-associations", required=True)
    parser.add_argument("--covered-positions", required=True)
    parser.add_argument("--voteview-members", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--tau-days", type=float, default=365.2425)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    receipt = promote_and_group(
        provisional_episodes=args.provisional_episodes,
        senate_payroll=args.senate_payroll,
        authenticated_profiles=args.authenticated_profiles,
        registrant_associations=args.registrant_associations,
        covered_positions=args.covered_positions,
        voteview_members=args.voteview_members,
        output_root=args.output_root,
        as_of=args.as_of,
        tau_days=args.tau_days,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
