# -*- coding: utf-8 -*-
"""Extract a restricted source-native staffer/industry plane from reconciled LDA.

This module performs no canonical identity resolution.  LDA lobbyists remain
``lda:lobbyist:<id>`` source people, registrants and clients retain distinct
namespaces, and covered-position text remains an assertion rather than a
Hill-to-lobbying transition claim.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Iterator, Mapping

FEC_ROOT = Path(__file__).resolve().parents[1]
if str(FEC_ROOT) not in sys.path:
    sys.path.insert(0, str(FEC_ROOT))

from staffer_network.lda_composite_repair import (
    LEGACY_YEARS, _iter_unique_legacy, _load_window_payloads, _timestamp_key,
)
from staffer_network.source_manifest import canonical_json_bytes, sha256_file


class LdaCorpusError(RuntimeError):
    pass


CONTACT_KEY_RE = re.compile(
    r"(?:contact|telephone|phone|email|address|zip)$", re.IGNORECASE
)
GOVERNMENT_TERMS = (
    "CONGRESS", "CONGRESSIONAL", "SENATE", "SENATOR", "HOUSE",
    "REPRESENTATIVE", "LEGISLATIVE", "COMMITTEE", "WHITE HOUSE",
    "EXECUTIVE OFFICE", "FEDERAL", "GOVERNMENT",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _norm_space(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    return normalized or None


def _source_id(kind: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LdaCorpusError(f"invalid LDA {kind} id {value!r}")
    return f"lda:{kind}:{value}"


def _display_name(lobbyist: Mapping[str, Any]) -> str:
    values = [
        lobbyist.get("prefix"), lobbyist.get("first_name"),
        lobbyist.get("nickname"), lobbyist.get("middle_name"),
        lobbyist.get("last_name"), lobbyist.get("suffix"),
    ]
    name = " ".join(value.strip() for value in values if isinstance(value, str) and value.strip())
    if not name:
        raise LdaCorpusError("LDA lobbyist has no usable name")
    return name


def _record_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _edge(
    *, predicate: str, subject_id: str, subject_kind: str,
    object_id: str, object_kind: str, support: Mapping[str, Any],
) -> dict[str, Any]:
    digest = _record_digest(support)
    return {
        "schema_id": "staffer-network-lda-industry-edge-v1",
        "edge_id": "lda:edge:" + hashlib.sha256(
            f"{predicate}\0{subject_id}\0{object_id}\0{digest}".encode("utf-8")
        ).hexdigest(),
        "predicate": predicate,
        "subject_id": subject_id,
        "subject_kind": subject_kind,
        "object_id": object_id,
        "object_kind": object_kind,
        "raw_weight": 1.0,
        "precision_bucket": "day",
        "support_count": 1,
        "support_digest": digest,
    }


def _ordered_composite_filings(
    legacy_root: Path,
    composite_root: Path,
    receipt: Mapping[str, Any],
) -> Iterator[tuple[int, Mapping[str, Any]]]:
    windows = receipt["windows"]
    expected = {row["year"]: row["composite_unique_filing_uuids"] for row in receipt["per_year"]}
    for year in LEGACY_YEARS:
        filings = {
            filing_uuid: filing
            for filing_uuid, filing in _iter_unique_legacy(legacy_root, year)
        }
        for window in windows:
            if window["filing_year"] != year:
                continue
            filings.update(
                _load_window_payloads(composite_root, window["window_id"], "B")
            )
        if len(filings) != expected[year]:
            raise LdaCorpusError(
                f"{year}: composite extraction found {len(filings)} filings; "
                f"receipt requires {expected[year]}"
            )
        for filing_uuid in sorted(
            filings,
            key=lambda value: (str(filings[value].get("dt_posted")), value),
        ):
            yield year, filings[filing_uuid]


@contextmanager
def _gzip_writer(path: Path):
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0
            ) as stream:
                yield stream
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_row(stream, row: Mapping[str, Any]) -> None:
    stream.write(canonical_json_bytes(row))


def _write_sorted_gzip(path: Path, rows: Iterable[Mapping[str, Any]], key) -> int:
    ordered = sorted(rows, key=key)
    with _gzip_writer(path) as stream:
        for row in ordered:
            _write_row(stream, row)
    return len(ordered)


def _activity_id(filing_uuid: str, ordinal: int) -> str:
    return f"lda:activity:{filing_uuid}:{ordinal}"


def _government_terms(value: str | None) -> list[str]:
    """Return unadjudicated lexical substring hits, never employment facts."""
    if not value:
        return []
    upper = value.upper()
    return [term for term in GOVERNMENT_TERMS if term in upper]


def _minimized_organization(
    organization: Mapping[str, Any], *, kind: str
) -> dict[str, Any]:
    source_id = _source_id(kind, organization.get("id"))
    name = _norm_space(organization.get("name"))
    if not name:
        raise LdaCorpusError(f"{source_id}: missing organization name")
    description_key = "description" if kind == "registrant" else "general_description"
    return {
        "source_org_id": source_id,
        "source_numeric_id": organization["id"],
        "organization_kind": kind,
        "name": name,
        "description": _norm_space(organization.get(description_key)),
        "country": _norm_space(organization.get("country")),
        "state": _norm_space(organization.get("state")),
    }


def build_lda_source_native_corpus(
    legacy_root: str | Path,
    composite_root: str | Path,
    output_root: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    legacy_root = Path(legacy_root).resolve()
    composite_root = Path(composite_root).resolve()
    output_root = Path(output_root).resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise LdaCorpusError(f"output root is nonempty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    receipt_path = composite_root / "composite_reconciliation_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS":
        raise LdaCorpusError("C-5 composite receipt is not PASS")
    if receipt["downstream_gates"]["identity_resolution"] != "CLOSED":
        raise LdaCorpusError("unexpected upstream identity gate state")

    lobbyists: dict[str, dict[str, Any]] = {}
    organizations: dict[str, dict[str, Any]] = {}
    organization_support: Counter[str] = Counter()
    file_counts = {
        "activity_assertions": 0,
        "industry_edges": 0,
        "covered_position_assertions": 0,
    }
    activity_path = output_root / "lda_activity_assertions.ndjson.gz"
    edge_path = output_root / "lda_industry_edges.ndjson.gz"
    covered_path = output_root / "lda_covered_position_assertions.ndjson.gz"
    filing_count = 0
    activity_ids: set[str] = set()
    edge_ids: set[str] = set()
    covered_ids: set[str] = set()
    with _gzip_writer(activity_path) as activity_stream, _gzip_writer(
        edge_path
    ) as edge_stream, _gzip_writer(covered_path) as covered_stream:
        for year, filing in _ordered_composite_filings(
            legacy_root, composite_root, receipt
        ):
            filing_count += 1
            filing_uuid = filing.get("filing_uuid")
            if not isinstance(filing_uuid, str) or not filing_uuid:
                raise LdaCorpusError("filing lacks UUID")
            raw_dt_posted = filing.get("dt_posted")
            if not isinstance(raw_dt_posted, str):
                raise LdaCorpusError(f"{filing_uuid}: filing lacks dt_posted")
            dt_posted = _timestamp_key(
                raw_dt_posted, label=f"source-native filing {filing_uuid}"
            )
            registrant = filing.get("registrant")
            client = filing.get("client")
            if not isinstance(registrant, dict) or not isinstance(client, dict):
                raise LdaCorpusError(f"{filing_uuid}: missing registrant/client")
            reg = _minimized_organization(registrant, kind="registrant")
            cli = _minimized_organization(client, kind="client")
            for row in (reg, cli):
                source_org_id = row["source_org_id"]
                prior = organizations.get(source_org_id)
                if prior is None:
                    organizations[source_org_id] = {
                        **row,
                        "name_variants": {row["name"]},
                        "description_variants": ({row["description"]} if row["description"] else set()),
                        "country_variants": ({row["country"]} if row["country"] else set()),
                        "state_variants": ({row["state"]} if row["state"] else set()),
                    }
                else:
                    if prior["organization_kind"] != row["organization_kind"]:
                        raise LdaCorpusError(f"organization namespace collision {source_org_id}")
                    prior["name_variants"].add(row["name"])
                    for field in ("description", "country", "state"):
                        if row[field]:
                            prior[field + "_variants"].add(row[field])
            activities = filing.get("lobbying_activities") or []
            if not isinstance(activities, list):
                raise LdaCorpusError(f"{filing_uuid}: activities is not a list")
            for ordinal, activity in enumerate(activities):
                if not isinstance(activity, dict):
                    raise LdaCorpusError(f"{filing_uuid}:{ordinal}: activity is not an object")
                source_activity_id = _activity_id(filing_uuid, ordinal)
                if source_activity_id in activity_ids:
                    raise LdaCorpusError(f"duplicate activity ID {source_activity_id}")
                activity_ids.add(source_activity_id)
                government_entities = []
                for entity in activity.get("government_entities") or []:
                    if not isinstance(entity, dict):
                        continue
                    government_entities.append({
                        "id": entity.get("id"),
                        "name": _norm_space(entity.get("name")),
                    })
                activity_row = {
                    "schema_id": "staffer-network-lda-activity-assertion-v1",
                    "source_system_id": "senate-lda-api",
                    "source_activity_id": source_activity_id,
                    "filing_uuid": filing_uuid,
                    "filing_year": year,
                    "activity_ordinal": ordinal,
                    "dt_posted": dt_posted,
                    "filing_period": filing.get("filing_period"),
                    "general_issue_code": activity.get("general_issue_code"),
                    "general_issue_code_display": _norm_space(activity.get("general_issue_code_display")),
                    "description": _norm_space(activity.get("description")),
                    "registrant_source_org_id": reg["source_org_id"],
                    "client_source_org_id": cli["source_org_id"],
                    "government_entities": government_entities,
                }
                _write_row(activity_stream, activity_row)
                file_counts["activity_assertions"] += 1
                organization_support[reg["source_org_id"]] += 1
                organization_support[cli["source_org_id"]] += 1
                support = {
                    "filing_uuid": filing_uuid,
                    "activity_ordinal": ordinal,
                    "dt_posted": dt_posted,
                }
                for edge in (
                    _edge(
                        predicate="FILED_BY", subject_id=source_activity_id,
                        subject_kind="lda_activity", object_id=reg["source_org_id"],
                        object_kind="registrant", support={**support, "role": "registrant"},
                    ),
                    _edge(
                        predicate="FOR_CLIENT", subject_id=source_activity_id,
                        subject_kind="lda_activity", object_id=cli["source_org_id"],
                        object_kind="client", support={**support, "role": "client"},
                    ),
                ):
                    if edge["edge_id"] in edge_ids:
                        raise LdaCorpusError(f"duplicate edge ID {edge['edge_id']}")
                    edge_ids.add(edge["edge_id"])
                    _write_row(edge_stream, edge)
                    file_counts["industry_edges"] += 1
                seen_activity_lobbyists: set[str] = set()
                for item_ordinal, item in enumerate(activity.get("lobbyists") or []):
                    if not isinstance(item, dict) or not isinstance(item.get("lobbyist"), dict):
                        raise LdaCorpusError(f"{source_activity_id}: malformed lobbyist item")
                    raw_lobbyist = item["lobbyist"]
                    source_person_id = _source_id("lobbyist", raw_lobbyist.get("id"))
                    name = _display_name(raw_lobbyist)
                    person = lobbyists.setdefault(source_person_id, {
                        "source_numeric_id": raw_lobbyist["id"],
                        "name_variants": set(),
                        "first_name_variants": set(),
                        "middle_name_variants": set(),
                        "last_name_variants": set(),
                        "suffix_variants": set(),
                        "filing_years": set(),
                        "first_dt_posted": dt_posted,
                        "last_dt_posted": dt_posted,
                        "activity_support_count": 0,
                        "covered_position_support_count": 0,
                        "covered_position_values": set(),
                    })
                    person["name_variants"].add(name)
                    for source_field, target in (
                        ("first_name", "first_name_variants"),
                        ("middle_name", "middle_name_variants"),
                        ("last_name", "last_name_variants"),
                        ("suffix", "suffix_variants"),
                    ):
                        value = _norm_space(raw_lobbyist.get(source_field))
                        if value:
                            person[target].add(value)
                    person["filing_years"].add(year)
                    person["first_dt_posted"] = min(person["first_dt_posted"], dt_posted)
                    person["last_dt_posted"] = max(person["last_dt_posted"], dt_posted)
                    if source_person_id not in seen_activity_lobbyists:
                        seen_activity_lobbyists.add(source_person_id)
                        person["activity_support_count"] += 1
                        named_edge = _edge(
                            predicate="NAMED_ON_ACTIVITY",
                            subject_id=source_person_id, subject_kind="person",
                            object_id=source_activity_id, object_kind="lda_activity",
                            support={**support, "lobbyist_source_person_id": source_person_id},
                        )
                        if named_edge["edge_id"] in edge_ids:
                            raise LdaCorpusError(f"duplicate edge ID {named_edge['edge_id']}")
                        edge_ids.add(named_edge["edge_id"])
                        _write_row(edge_stream, named_edge)
                        file_counts["industry_edges"] += 1
                    covered_position = _norm_space(item.get("covered_position"))
                    if covered_position:
                        person["covered_position_support_count"] += 1
                        person["covered_position_values"].add(covered_position)
                        covered_support = {
                            **support,
                            "lobbyist_item_ordinal": item_ordinal,
                            "lobbyist_source_person_id": source_person_id,
                            "covered_position": covered_position,
                        }
                        assertion_id = "lda:covered-position:" + _record_digest(covered_support)
                        if assertion_id in covered_ids:
                            raise LdaCorpusError(f"duplicate covered-position assertion {assertion_id}")
                        covered_ids.add(assertion_id)
                        covered_row = {
                            "schema_id": "staffer-network-lda-covered-position-assertion-v1",
                            "source_system_id": "senate-lda-api",
                            "assertion_id": assertion_id,
                            "source_person_id": source_person_id,
                            "source_activity_id": source_activity_id,
                            "filing_uuid": filing_uuid,
                            "dt_posted": dt_posted,
                            "covered_position": covered_position,
                            "government_term_hits": _government_terms(covered_position),
                            "support_digest": _record_digest(covered_support),
                        }
                        _write_row(covered_stream, covered_row)
                        file_counts["covered_position_assertions"] += 1

    lobbyist_rows = []
    for source_person_id, person in lobbyists.items():
        lobbyist_rows.append({
            "schema_id": "staffer-network-lda-lobbyist-assertion-v1",
            "source_system_id": "senate-lda-api",
            "source_person_id": source_person_id,
            "source_numeric_id": person["source_numeric_id"],
            "display_name": sorted(person["name_variants"], key=lambda x: (x.casefold(), x))[0],
            "name_variants": sorted(person["name_variants"], key=lambda x: (x.casefold(), x)),
            "first_name_variants": sorted(person["first_name_variants"], key=lambda x: (x.casefold(), x)),
            "middle_name_variants": sorted(person["middle_name_variants"], key=lambda x: (x.casefold(), x)),
            "last_name_variants": sorted(person["last_name_variants"], key=lambda x: (x.casefold(), x)),
            "suffix_variants": sorted(person["suffix_variants"], key=lambda x: (x.casefold(), x)),
            "filing_years": sorted(person["filing_years"]),
            "first_dt_posted": person["first_dt_posted"],
            "last_dt_posted": person["last_dt_posted"],
            "activity_support_count": person["activity_support_count"],
            "covered_position_support_count": person["covered_position_support_count"],
            "covered_position_values": sorted(person["covered_position_values"], key=lambda x: (x.casefold(), x)),
        })
    organization_rows = []
    for source_org_id, organization in organizations.items():
        organization_rows.append({
            "schema_id": "staffer-network-lda-organization-assertion-v1",
            "source_system_id": "senate-lda-api",
            "source_org_id": source_org_id,
            "source_numeric_id": organization["source_numeric_id"],
            "organization_kind": organization["organization_kind"],
            "display_name": sorted(organization["name_variants"], key=lambda x: (x.casefold(), x))[0],
            "name_variants": sorted(organization["name_variants"], key=lambda x: (x.casefold(), x)),
            "description_variants": sorted(organization["description_variants"], key=lambda x: (x.casefold(), x)),
            "country_variants": sorted(organization["country_variants"]),
            "state_variants": sorted(organization["state_variants"]),
            "activity_support_count": organization_support[source_org_id],
        })
    lobbyist_path = output_root / "lda_lobbyist_assertions.ndjson.gz"
    organization_path = output_root / "lda_organization_assertions.ndjson.gz"
    lobbyist_count = _write_sorted_gzip(
        lobbyist_path, lobbyist_rows, key=lambda row: row["source_person_id"]
    )
    organization_count = _write_sorted_gzip(
        organization_path, organization_rows, key=lambda row: row["source_org_id"]
    )
    files = []
    for role, path, records, schema_id in (
        ("lobbyist_assertions", lobbyist_path, lobbyist_count, "staffer-network-lda-lobbyist-assertion-v1"),
        ("organization_assertions", organization_path, organization_count, "staffer-network-lda-organization-assertion-v1"),
        ("activity_assertions", activity_path, file_counts["activity_assertions"], "staffer-network-lda-activity-assertion-v1"),
        ("industry_edges", edge_path, file_counts["industry_edges"], "staffer-network-lda-industry-edge-v1"),
        ("covered_position_assertions", covered_path, file_counts["covered_position_assertions"], "staffer-network-lda-covered-position-assertion-v1"),
    ):
        files.append({
            "role": role,
            "path": path.name,
            "schema_id": schema_id,
            "records": records,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "encoding": "canonical-ndjson-gzip-mtime-0",
            "privacy_class": "restricted-machine-local",
        })
    generated_at = generated_at or _now()
    output_receipt = {
        "schema_id": "staffer-network-lda-source-native-corpus-receipt-v1",
        "generated_at": generated_at,
        "status": "PASS",
        "source_system_id": "senate-lda-api",
        "composite_receipt_sha256": sha256_file(receipt_path),
        "cutoff_exclusive": receipt["cutoff_exclusive"],
        "filings": filing_count,
        "lobbyists": lobbyist_count,
        "registrants": sum(1 for row in organization_rows if row["organization_kind"] == "registrant"),
        "clients": sum(1 for row in organization_rows if row["organization_kind"] == "client"),
        "activities": file_counts["activity_assertions"],
        "industry_edges": file_counts["industry_edges"],
        "covered_position_assertions": file_counts["covered_position_assertions"],
        "lobbyists_with_covered_position": sum(1 for row in lobbyist_rows if row["covered_position_support_count"] > 0),
        "lobbyists_with_government_term_hit": sum(1 for row in lobbyist_rows if any(_government_terms(value) for value in row["covered_position_values"])),
        "files": files,
        "canonical_identity_merges": 0,
        "cross_source_identity_assertions": 0,
        "name_matches_are_candidates_only": True,
        "analysis_enabled": False,
        "publication_enabled": False,
        "staff_net_required_state": "off",
        "failures": [],
    }
    receipt_out = output_root / "lda_source_native_corpus_receipt.json"
    temporary = receipt_out.with_suffix(f".json.tmp.{os.getpid()}")
    temporary.write_bytes(canonical_json_bytes(output_receipt))
    os.replace(temporary, receipt_out)
    return output_receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-sweeps-root", required=True)
    parser.add_argument("--composite-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--generated-at")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        receipt = build_lda_source_native_corpus(
            args.legacy_sweeps_root,
            args.composite_root,
            args.output_root,
            generated_at=args.generated_at,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
