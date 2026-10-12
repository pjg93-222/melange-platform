#!/usr/bin/env python3
"""Audit complete, lossless visibility of a staged BILLSTATUS enrichment run."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS
try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import atomic_write_bytes


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "billstatus-enrichment-completeness-audit-v1"
STATUS = "PASS_WITH_VISIBLE_GAPS"
LEDGER_LABELS = (
    "candidate_visibility_gaps",
    "field_completeness_gaps",
    "role_provenance_gaps",
)
GAP_BASE_FIELDS = {
    "canonical_key",
    "gap_kind",
    "field",
    "scope",
    "item_index",
    "detail",
    "candidate_retained",
    "absence_is_neutral",
    "automatic_exclusion_authorized",
    "automatic_scoring_authorized",
}
GAP_PROVENANCE_FIELDS = {
    "advanced_search_line",
    "advanced_search_line_sha256",
}
UNSTRUCTURED_RELEVANCE_SECTIONS = {
    "policyArea": "source_policy_area_not_structured",
    "subjects": "source_legislative_subjects_not_structured",
    "committees": "source_committees_not_structured",
    "relatedBills": "source_related_bills_not_structured",
    "amendments": "source_amendments_not_structured",
    "committeeReports": "source_committee_reports_not_structured",
    "cboCostEstimates": "source_cbo_cost_estimates_not_structured",
    "laws": "source_laws_not_structured",
}


class CompletenessError(ValueError):
    """Raised when the staged enrichment does not close exactly."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CompletenessError(message)


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON object required: {path}")
    return value


def atomic_write(path: Path, body: bytes) -> None:
    # Was ``path.with_name(path.name + ".tmp")``: a fixed scratch name that two
    # concurrent audits writing the same output silently clobbered, and that
    # OneDrive syncs and conflict-copies.  ``atomic_write_bytes`` is unique per
    # process and per call, fsync'd before publication, and retries the publish
    # through the OneDrive/AV lock window.
    atomic_write_bytes(path, body)


def ndjson_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
        for row in rows
    )


def _official_text_link(url: str) -> bool:
    parsed = urlparse(url)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname in {"www.govinfo.gov", "www.congress.gov"}
        and parsed.username is None
        and parsed.password is None
    )


def _gap(
    *,
    canonical_key: str,
    gap_kind: str,
    field: str,
    scope: str,
    index: int | None = None,
    detail: Any = None,
) -> dict[str, Any]:
    return {
        "canonical_key": canonical_key,
        "gap_kind": gap_kind,
        "field": field,
        "scope": scope,
        "item_index": index,
        "detail": detail,
        "candidate_retained": True,
        "absence_is_neutral": True,
        "automatic_exclusion_authorized": False,
        "automatic_scoring_authorized": False,
    }


def _source_section_detail(
    section: ET.Element,
    *,
    source_path: str,
) -> dict[str, Any]:
    leaf_text_count = sum(
        1
        for node in section.iter()
        if node is not section
        and len(node) == 0
        and node.text
        and node.text.strip()
    )
    return {
        "source_path": source_path,
        "raw_source_preserved": True,
        "structured_parser_field": None,
        "direct_child_count": len(section),
        "leaf_text_count": leaf_text_count,
    }


def build_audit(
    worker_manifest_path: Path,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    worker_manifest_path = worker_manifest_path.resolve()
    run_dir = worker_manifest_path.parent
    worker = read_object(worker_manifest_path)
    require(
        worker.get("schema") == "govinfo-billstatus-enrichment-run-v1"
        and worker.get("status") == "COMPLETE",
        "worker manifest is not a complete BILLSTATUS run",
    )
    for field in (
        "production_write_performed",
        "sync_or_promotion_performed",
        "candidate_creation_performed",
        "semantic_exclusion_performed",
    ):
        require(worker.get(field) is False, f"unsafe worker flag: {field}")
    require(worker.get("official_source_only") is True, "worker source drift")
    require(worker.get("failures") == [], "worker failures are not empty")
    require(
        (worker.get("sitemaps") or {}).get("required") == 32
        and (worker.get("sitemaps") or {}).get("validated") == 32,
        "official sitemap validation is not exact 32/32",
    )

    plan_receipt = worker.get("plan")
    require(isinstance(plan_receipt, dict), "worker plan receipt missing")
    plan_path = Path(str(plan_receipt.get("path") or "")).resolve()
    require(
        plan_path.is_file()
        and plan_receipt.get("bytes") == plan_path.stat().st_size
        and plan_receipt.get("sha256") == BILLSTATUS.sha256_file(plan_path),
        "worker plan receipt drift",
    )
    plan = read_object(plan_path)
    strict_receipt = (plan.get("snapshot_contract") or {}).get(
        "strict_manifest"
    )
    require(isinstance(strict_receipt, dict), "plan strict receipt missing")
    BILLSTATUS.validate_snapshot_plan(
        plan,
        expected_strict_manifest=Path(str(strict_receipt.get("path") or "")),
    )
    tasks = {
        str(task["canonical_key"]): task for task in plan["record_tasks"]
    }
    receipts = worker.get("record_receipts")
    require(isinstance(receipts, list), "worker record receipts missing")
    receipt_keys = [
        str(receipt.get("canonical_key"))
        for receipt in receipts
        if isinstance(receipt, dict)
    ]
    require(
        len(receipt_keys) == len(receipts) == len(set(receipt_keys)),
        "worker record receipt keys are invalid or duplicate",
    )
    require(
        set(receipt_keys) == set(tasks),
        "worker receipts do not exactly close over planned top-level keys",
    )
    partition = worker.get("partition") or {}
    require(
        partition.get("all_candidates")
        == plan["candidate_partition"]["all"]
        and partition.get("top_level_planned") == len(tasks)
        and partition.get("top_level_selected_this_run") == len(tasks)
        and partition.get("top_level_completed") == len(tasks)
        and partition.get("top_level_failed") == 0
        and partition.get("remaining_top_level") == 0
        and partition.get("nested_only_amendments_retained_unresolved")
        == len(plan["unsupported_candidates"]),
        "worker candidate partition drift",
    )

    candidate_gaps: list[dict[str, Any]] = []
    field_gaps: list[dict[str, Any]] = []
    role_gaps: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    unique_bioguides: set[str] = set()
    raw_hashes: set[str] = set()
    parsed_hashes: set[str] = set()

    for receipt in receipts:
        assert isinstance(receipt, dict)
        key = str(receipt["canonical_key"])
        task = tasks[key]
        raw_path = Path(str(receipt.get("raw_path") or "")).resolve()
        parsed_path = Path(str(receipt.get("parsed_path") or "")).resolve()
        require(
            raw_path.is_relative_to(run_dir)
            and parsed_path.is_relative_to(run_dir),
            f"record paths escape staged run: {key}",
        )
        require(
            receipt.get("schema") == "govinfo-billstatus-record-receipt-v1"
            and receipt.get("source_url") == task["url"]
            and receipt.get("advanced_search_line")
            == task["advanced_search_line"]
            and receipt.get("advanced_search_line_sha256")
            == task["advanced_search_line_sha256"]
            and receipt.get("candidate_creation_performed") is False
            and receipt.get("semantic_exclusion_performed") is False,
            f"record receipt provenance drift: {key}",
        )
        require(
            raw_path.is_file()
            and raw_path.stat().st_size == receipt.get("raw_bytes"),
            f"raw file receipt drift: {key}",
        )
        parsed = BILLSTATUS.parse_billstatus_xml(
            raw_path,
            source_url=task["url"],
            sitemap_lastmod=str(receipt["sitemap_lastmod"]),
            expected_sha256=str(receipt["raw_sha256"]),
        )
        source_root = ET.fromstring(raw_path.read_bytes())
        source_bill = source_root.find("bill")
        require(source_bill is not None, f"raw source bill missing: {key}")
        assert source_bill is not None
        require(
            parsed_path.is_file()
            and parsed_path.stat().st_size == receipt.get("parsed_bytes"),
            f"parsed file receipt drift: {key}",
        )
        parsed_raw = parsed_path.read_bytes()
        require(
            BILLSTATUS.sha256_bytes(parsed_raw) == receipt.get("parsed_sha256"),
            f"parsed file hash drift: {key}",
        )
        stored = json.loads(parsed_raw.decode("utf-8"))
        require(
            stored == parsed and parsed.get("canonical_key") == key,
            f"parsed semantic/identity drift: {key}",
        )
        raw_hashes.add(str(receipt["raw_sha256"]))
        parsed_hashes.add(str(receipt["parsed_sha256"]))
        counts["parsed_records"] += 1

        titles = list(parsed.get("titles") or [])
        summaries = list(parsed.get("summaries") or [])
        actions = list(parsed.get("actions") or [])
        votes = list(parsed.get("recorded_votes") or [])
        text_versions = list(parsed.get("text_versions") or [])
        sponsors = list(parsed.get("sponsors") or [])
        cosponsors = list(parsed.get("cosponsors") or [])
        role_counts["sponsor_items"] += len(sponsors)
        role_counts["cosponsor_items"] += len(cosponsors)
        if sponsors:
            role_counts["records_with_sponsors"] += 1
        else:
            candidate_gaps.append(
                _gap(
                    canonical_key=key,
                    gap_kind="no_sponsors",
                    field="sponsors",
                    scope="candidate_relationship_absence",
                )
            )
        if cosponsors:
            role_counts["records_with_cosponsors"] += 1
        else:
            candidate_gaps.append(
                _gap(
                    canonical_key=key,
                    gap_kind="no_cosponsors",
                    field="cosponsors",
                    scope="candidate_relationship_absence",
                )
            )
        text_urls = [
            url
            for version in text_versions
            for url in (version.get("urls") or [])
            if isinstance(url, str) and url
        ]
        for label, rows in (
            ("title_items", titles),
            ("summary_items", summaries),
            ("action_items", actions),
            ("recorded_vote_items", votes),
            ("text_version_items", text_versions),
            ("text_link_items", text_urls),
        ):
            counts[label] += len(rows)
        for label, rows, field in (
            ("records_with_titles", titles, "titles"),
            ("records_with_summaries", summaries, "summaries"),
            ("records_with_actions", actions, "actions"),
            ("records_with_recorded_votes", votes, "recorded_votes"),
            ("records_with_text_versions", text_versions, "text_versions"),
            ("records_with_text_links", text_urls, "text_links"),
        ):
            if rows:
                counts[label] += 1
            else:
                candidate_gaps.append(
                    _gap(
                        canonical_key=key,
                        gap_kind=f"no_{field}",
                        field=field,
                        scope="candidate_evidence_absence",
                    )
                )

        if not parsed.get("record_update_date"):
            field_gaps.append(
                _gap(
                    canonical_key=key,
                    gap_kind="missing_record_update_date",
                    field="record_update_date",
                    scope="record",
                )
            )
        if not parsed.get("record_update_date_including_text"):
            field_gaps.append(
                _gap(
                    canonical_key=key,
                    gap_kind="missing_update_date_including_text",
                    field="record_update_date_including_text",
                    scope="record",
                )
            )
        for index, title in enumerate(titles):
            for field in ("title", "title_type"):
                if not title.get(field):
                    field_gaps.append(
                        _gap(
                            canonical_key=key,
                            gap_kind=f"title_{field}_missing",
                            field=field,
                            scope="title",
                            index=index,
                        )
                    )
        for index, summary in enumerate(summaries):
            for field in ("text", "action_date"):
                if not summary.get(field):
                    field_gaps.append(
                        _gap(
                            canonical_key=key,
                            gap_kind=f"summary_{field}_missing",
                            field=field,
                            scope="summary",
                            index=index,
                        )
                    )
        for index, action in enumerate(actions):
            for field in (
                "action_date",
                "action_code",
                "action_type",
                "text",
                "source_system_code",
                "source_system_name",
            ):
                if not action.get(field):
                    field_gaps.append(
                        _gap(
                            canonical_key=key,
                            gap_kind=f"action_{field}_missing",
                            field=field,
                            scope="action",
                            index=index,
                        )
                    )
        for index, vote in enumerate(votes):
            for field in (
                "chamber",
                "congress",
                "date",
                "roll_number",
                "session_number",
                "url",
            ):
                if not vote.get(field):
                    field_gaps.append(
                        _gap(
                            canonical_key=key,
                            gap_kind=f"vote_{field}_missing",
                            field=field,
                            scope="recorded_vote",
                            index=index,
                        )
                    )
        for index, version in enumerate(text_versions):
            for field in ("date", "type"):
                if not version.get(field):
                    field_gaps.append(
                        _gap(
                            canonical_key=key,
                            gap_kind=f"text_version_{field}_missing",
                            field=field,
                            scope="text_version",
                            index=index,
                        )
                    )
            version_urls = list(version.get("urls") or [])
            if not version_urls:
                field_gaps.append(
                    _gap(
                        canonical_key=key,
                        gap_kind="text_version_urls_missing",
                        field="urls",
                        scope="text_version",
                        index=index,
                    )
                )
            for url in version_urls:
                if not _official_text_link(str(url)):
                    field_gaps.append(
                        _gap(
                            canonical_key=key,
                            gap_kind="text_link_nonofficial_or_invalid",
                            field="url",
                            scope="text_version",
                            index=index,
                            detail=url,
                        )
                    )

        for container, relationships in (
            ("sponsor", sponsors),
            ("cosponsor", cosponsors),
        ):
            for index, relationship in enumerate(relationships):
                role = relationship.get("relationship_role")
                status = relationship.get("relationship_status")
                if role == "Sponsor":
                    role_counts["sponsor_relationships"] += 1
                elif role == "Original Cosponsor":
                    role_counts["original_cosponsor_relationships"] += 1
                elif role == "Cosponsor":
                    role_counts["later_cosponsor_relationships"] += 1
                else:
                    role_counts["unresolved_role_relationships"] += 1
                    role_gaps.append(
                        _gap(
                            canonical_key=key,
                            gap_kind="relationship_role_unresolved",
                            field="relationship_role",
                            scope=container,
                            index=index,
                            detail=relationship.get("role_provenance"),
                        )
                    )
                if status == "withdrawn":
                    role_counts["withdrawn_relationships"] += 1
                bioguide = relationship.get("bioguide_id")
                if bioguide:
                    role_counts["relationships_with_bioguide"] += 1
                    unique_bioguides.add(str(bioguide))
                else:
                    role_counts["relationships_missing_bioguide"] += 1
                    role_gaps.append(
                        _gap(
                            canonical_key=key,
                            gap_kind="relationship_bioguide_missing",
                            field="bioguide_id",
                            scope=container,
                            index=index,
                            detail=role,
                        )
                    )
                if not relationship.get("full_name"):
                    role_counts["relationships_missing_full_name"] += 1
                    role_gaps.append(
                        _gap(
                            canonical_key=key,
                            gap_kind="relationship_full_name_missing",
                            field="full_name",
                            scope=container,
                            index=index,
                            detail=role,
                        )
                    )
                expected_role_provenance = {
                    "Sponsor": "bill/sponsors/item",
                    "Original Cosponsor": (
                        "bill/cosponsors/item/isOriginalCosponsor=true"
                    ),
                    "Cosponsor": (
                        "bill/cosponsors/item/isOriginalCosponsor=false"
                    ),
                }.get(role)
                require(
                    relationship.get("role_provenance_complete")
                    is (role is not None)
                    and relationship.get("role_provenance")
                    == (
                        expected_role_provenance
                        if expected_role_provenance is not None
                        else (
                            "bill/cosponsors/item/"
                            "isOriginalCosponsor=missing"
                        )
                    )
                    and (
                        (container == "sponsor" and role == "Sponsor")
                        or (
                            container == "cosponsor"
                            and (
                                (
                                    relationship.get("original_cosponsor")
                                    is True
                                    and role == "Original Cosponsor"
                                )
                                or (
                                    relationship.get("original_cosponsor")
                                    is False
                                    and role == "Cosponsor"
                                )
                                or (
                                    relationship.get("original_cosponsor")
                                    is None
                                    and role is None
                                )
                            )
                        )
                    )
                    and (
                        (status == "withdrawn")
                        is bool(relationship.get("withdrawn_date"))
                    ),
                    f"relationship role/status invariant drift: {key} "
                    f"{container}[{index}]",
                )
                role_counts["relationship_rows"] += 1
            relationship_histories: dict[str, list[tuple[int, dict[str, Any]]]]
            relationship_histories = {}
            for index, relationship in enumerate(relationships):
                identity = str(relationship.get("bioguide_id") or "")
                if identity:
                    relationship_histories.setdefault(identity, []).append(
                        (index, relationship)
                    )
            for identity, history in relationship_histories.items():
                if len(history) < 2:
                    continue
                role_counts["repeated_member_relationship_groups"] += 1
                role_counts["repeated_member_relationship_rows"] += len(
                    history
                )
                role_gaps.append(
                    _gap(
                        canonical_key=key,
                        gap_kind="repeated_member_relationship_history",
                        field="bioguide_id",
                        scope=container,
                        detail={
                            "bioguide_id": identity,
                            "event_count": len(history),
                            "events": [
                                {
                                    "item_index": item_index,
                                    "relationship_role": item.get(
                                        "relationship_role"
                                    ),
                                    "relationship_status": item.get(
                                        "relationship_status"
                                    ),
                                    "sponsorship_date": item.get(
                                        "sponsorship_date"
                                    ),
                                    "withdrawn_date": item.get(
                                        "withdrawn_date"
                                    ),
                                }
                                for item_index, item in history
                            ],
                            "automatic_deduplication_authorized": False,
                        },
                    )
                )

        for index, relationship in enumerate(
            source_bill.findall("./onBehalfOfSponsor/item")
        ):
            role_counts["on_behalf_of_sponsor_items"] += 1
            role_counts["unstructured_role_relationships"] += 1
            role_gaps.append(
                _gap(
                    canonical_key=key,
                    gap_kind="on_behalf_of_sponsor_not_structured",
                    field="onBehalfOfSponsor",
                    scope="role_relationship_unstructured",
                    index=index,
                    detail={
                        "source_path": "bill/onBehalfOfSponsor/item",
                        "bioguide_id": BILLSTATUS._text(
                            relationship,
                            "bioguideId",
                            "identifiers/bioguideId",
                        ),
                        "full_name": BILLSTATUS._text(
                            relationship, "fullName"
                        ),
                        "relationship_type": BILLSTATUS._text(
                            relationship, "type"
                        ),
                        "raw_source_preserved": True,
                    },
                )
            )

        for section_name, gap_kind in (
            UNSTRUCTURED_RELEVANCE_SECTIONS.items()
        ):
            section = source_bill.find(section_name)
            if section is None:
                continue
            counts["unstructured_relevance_sections"] += 1
            candidate_gaps.append(
                _gap(
                    canonical_key=key,
                    gap_kind=gap_kind,
                    field=section_name,
                    scope="candidate_relevance_evidence_unstructured",
                    detail=_source_section_detail(
                        section,
                        source_path=f"bill/{section_name}",
                    ),
                )
            )

    for amendment in plan["unsupported_candidates"]:
        candidate_gaps.append(
            {
                **_gap(
                    canonical_key=str(amendment["canonical_key"]),
                    gap_kind="nested_amendment_has_no_standalone_bulk_package",
                    field="billstatus_record",
                    scope="unsupported_nested_amendment",
                    detail="retained_unresolved",
                ),
                "advanced_search_line": amendment["advanced_search_line"],
                "advanced_search_line_sha256": amendment[
                    "advanced_search_line_sha256"
                ],
            }
        )

    all_gaps = candidate_gaps + field_gaps + role_gaps
    gap_counts = Counter(str(row["gap_kind"]) for row in all_gaps)
    count_payload = {
        **dict(sorted(counts.items())),
        "unique_raw_content_hashes": len(raw_hashes),
        "unique_parsed_content_hashes": len(parsed_hashes),
    }
    required_role_counts = (
        "relationship_rows",
        "sponsor_items",
        "cosponsor_items",
        "records_with_sponsors",
        "records_with_cosponsors",
        "sponsor_relationships",
        "original_cosponsor_relationships",
        "later_cosponsor_relationships",
        "withdrawn_relationships",
        "unresolved_role_relationships",
        "relationships_with_bioguide",
        "relationships_missing_bioguide",
        "relationships_missing_full_name",
        "on_behalf_of_sponsor_items",
        "unstructured_role_relationships",
        "repeated_member_relationship_groups",
        "repeated_member_relationship_rows",
    )
    role_payload = {
        **{
            key: int(role_counts.get(key, 0))
            for key in required_role_counts
        },
        "unique_bioguide_ids": len(unique_bioguides),
    }
    audit = {
        "schema": SCHEMA,
        "status": STATUS,
        "snapshot_only": True,
        "official_source_only": True,
        "network_executed": False,
        "production_write_performed": False,
        "sync_or_promotion_performed": False,
        "candidate_creation_performed": False,
        "candidate_exclusion_performed": False,
        "automatic_scoring_performed": False,
        "absence_is_neutral": True,
        "inputs": {
            "worker_manifest": BILLSTATUS.file_receipt(
                worker_manifest_path
            ),
            "snapshot_plan": BILLSTATUS.file_receipt(plan_path),
            "worker_producer": worker.get("producer"),
        },
        "closure": {
            "all_candidates": plan["candidate_partition"]["all"],
            "top_level_planned": len(tasks),
            "top_level_receipts_validated": len(receipts),
            "top_level_parsed_records_validated": counts["parsed_records"],
            "nested_amendments_retained_unresolved": len(
                plan["unsupported_candidates"]
            ),
            "candidate_partition_closed": (
                len(receipts) + len(plan["unsupported_candidates"])
                == plan["candidate_partition"]["all"]
            ),
            "planned_key_set_equals_receipt_key_set": True,
            "all_raw_hashes_validated": True,
            "all_parsed_hashes_validated": True,
            "all_xml_identities_validated": True,
        },
        "content_counts": count_payload,
        "relationship_counts": role_payload,
        "gap_counts": dict(sorted(gap_counts.items())),
        "gap_ledger_counts": {
            "candidate_visibility_gaps": len(candidate_gaps),
            "field_completeness_gaps": len(field_gaps),
            "role_provenance_gaps": len(role_gaps),
            "all_visible_gaps": len(all_gaps),
        },
        "acceptance_rule": (
            "Every planned top-level key has one hash- and identity-validated "
            "record receipt; every nested amendment and every defined evidence "
            "absence/incomplete field remains visible and is never automatic "
            "exclusion or scoring evidence."
        ),
    }
    return audit, candidate_gaps, field_gaps, role_gaps


def write_audit(
    out_root: Path,
    audit: dict[str, Any],
    candidate_gaps: list[dict[str, Any]],
    field_gaps: list[dict[str, Any]],
    role_gaps: list[dict[str, Any]],
) -> Path:
    input_hash = audit["inputs"]["worker_manifest"]["sha256"]
    producer_hash = BILLSTATUS.sha256_file(Path(__file__).resolve())
    audit_id = BILLSTATUS.stable_json_sha256(
        {
            "worker_manifest_sha256": input_hash,
            "producer_sha256": producer_hash,
        }
    )
    out_dir = out_root.resolve() / audit_id
    audit_path = out_dir / "completeness_audit.json"
    ledgers = {
        "candidate_visibility_gaps": (
            out_dir / "candidate_visibility_gaps.ndjson",
            candidate_gaps,
        ),
        "field_completeness_gaps": (
            out_dir / "field_completeness_gaps.ndjson",
            field_gaps,
        ),
        "role_provenance_gaps": (
            out_dir / "role_provenance_gaps.ndjson",
            role_gaps,
        ),
    }
    if audit_path.exists():
        existing = read_object(audit_path)
        validate_audit(audit_path)
        require(
            existing.get("inputs") == audit.get("inputs"),
            "existing content-addressed audit inputs drift",
        )
        return audit_path
    outputs: dict[str, Any] = {}
    for label, (path, rows) in ledgers.items():
        atomic_write(path, ndjson_bytes(rows))
        outputs[label] = {
            **BILLSTATUS.file_receipt(path),
            "record_count": len(rows),
        }
    final = {
        **audit,
        "audit_id": audit_id,
        "producer": BILLSTATUS.file_receipt(Path(__file__).resolve()),
        "outputs": outputs,
    }
    atomic_write(audit_path, json.dumps(final, indent=2).encode("utf-8") + b"\n")
    return audit_path


def validate_audit(path: Path) -> dict[str, Any]:
    audit = read_object(path)
    require(
        audit.get("schema") == SCHEMA
        and audit.get("status") == STATUS
        and audit.get("snapshot_only") is True
        and audit.get("network_executed") is False
        and audit.get("production_write_performed") is False
        and audit.get("sync_or_promotion_performed") is False
        and audit.get("candidate_creation_performed") is False
        and audit.get("candidate_exclusion_performed") is False
        and audit.get("automatic_scoring_performed") is False
        and audit.get("absence_is_neutral") is True,
        "completeness audit safety contract drift",
    )
    closure = audit.get("closure") or {}
    require(
        closure.get("candidate_partition_closed") is True
        and closure.get("planned_key_set_equals_receipt_key_set") is True
        and closure.get("all_raw_hashes_validated") is True
        and closure.get("all_parsed_hashes_validated") is True
        and closure.get("all_xml_identities_validated") is True
        and closure.get("top_level_planned")
        == closure.get("top_level_receipts_validated")
        == closure.get("top_level_parsed_records_validated")
        and closure.get("all_candidates")
        == closure.get("top_level_planned")
        + closure.get("nested_amendments_retained_unresolved"),
        "completeness audit closure drift",
    )
    inputs = audit.get("inputs") or {}
    worker_receipt = inputs.get("worker_manifest") or {}
    plan_receipt = inputs.get("snapshot_plan") or {}
    producer_receipt = audit.get("producer") or {}
    current_producer = Path(__file__).resolve()
    for label, receipt, expected_path in (
        (
            "worker manifest",
            worker_receipt,
            Path(str(worker_receipt.get("path") or "")),
        ),
        (
            "snapshot plan",
            plan_receipt,
            Path(str(plan_receipt.get("path") or "")),
        ),
        ("producer", producer_receipt, current_producer),
    ):
        require(
            expected_path.is_file()
            and Path(str(receipt.get("path") or "")).resolve()
            == expected_path.resolve()
            and receipt.get("bytes") == expected_path.stat().st_size
            and receipt.get("sha256")
            == BILLSTATUS.sha256_file(expected_path),
            f"completeness {label} receipt drift",
        )
    worker = read_object(Path(str(worker_receipt["path"])))
    worker_producer = worker.get("producer") or {}
    recorded_worker_producer = inputs.get("worker_producer") or {}
    worker_producer_path = Path(
        str(recorded_worker_producer.get("path") or "")
    )
    require(
        recorded_worker_producer == worker_producer
        and worker_producer_path.is_file()
        and recorded_worker_producer.get("bytes")
        == worker_producer_path.stat().st_size
        and recorded_worker_producer.get("sha256")
        == BILLSTATUS.sha256_file(worker_producer_path),
        "completeness worker producer receipt drift",
    )
    expected_audit_id = BILLSTATUS.stable_json_sha256(
        {
            "worker_manifest_sha256": worker_receipt.get("sha256"),
            "producer_sha256": producer_receipt.get("sha256"),
        }
    )
    require(
        audit.get("audit_id") == expected_audit_id
        and path.resolve().parent.name == expected_audit_id,
        "completeness content address drift",
    )
    outputs = audit.get("outputs") or {}
    require(
        isinstance(outputs, dict)
        and set(outputs) == set(LEDGER_LABELS),
        "completeness output ledger set drift",
    )
    plan = read_object(Path(str(plan_receipt["path"])))
    top_level_keys = {
        str(row["canonical_key"]) for row in plan["record_tasks"]
    }
    unsupported_keys = {
        str(row["canonical_key"]) for row in plan["unsupported_candidates"]
    }
    all_candidate_keys = top_level_keys | unsupported_keys
    observed_gap_counts: Counter[str] = Counter()
    observed_ledger_counts: dict[str, int] = {}
    for label in LEDGER_LABELS:
        receipt = outputs[label]
        ledger = Path(str((receipt or {}).get("path") or ""))
        require(
            ledger.is_file()
            and ledger.resolve().parent == path.resolve().parent
            and receipt.get("bytes") == ledger.stat().st_size
            and receipt.get("sha256") == BILLSTATUS.sha256_file(ledger),
            f"completeness gap ledger drift: {label}",
        )
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            ledger.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise CompletenessError(
                    f"invalid gap row JSON: {label}:{line_number}"
                ) from error
            require(
                isinstance(row, dict)
                and GAP_BASE_FIELDS.issubset(row)
                and set(row).issubset(
                    GAP_BASE_FIELDS | GAP_PROVENANCE_FIELDS
                ),
                f"invalid or leaking gap row fields: "
                f"{label}:{line_number}",
            )
            require(
                row.get("candidate_retained") is True
                and row.get("absence_is_neutral") is True
                and row.get("automatic_exclusion_authorized") is False
                and row.get("automatic_scoring_authorized") is False,
                f"unsafe gap row authority: {label}:{line_number}",
            )
            key = str(row.get("canonical_key") or "")
            allowed_keys = (
                all_candidate_keys
                if label == "candidate_visibility_gaps"
                else top_level_keys
            )
            require(
                key in allowed_keys,
                f"side-loaded gap candidate: {label}:{line_number}:{key}",
            )
            require(
                isinstance(row.get("gap_kind"), str)
                and bool(row["gap_kind"])
                and isinstance(row.get("field"), str)
                and bool(row["field"])
                and isinstance(row.get("scope"), str)
                and bool(row["scope"]),
                f"incomplete gap row identity: {label}:{line_number}",
            )
            observed_gap_counts[str(row["gap_kind"])] += 1
            rows.append(row)
        observed_ledger_counts[label] = len(rows)
        require(
            receipt.get("record_count") == len(rows),
            f"completeness gap ledger count drift: {label}",
        )
    recorded_ledger_counts = audit.get("gap_ledger_counts") or {}
    require(
        all(
            recorded_ledger_counts.get(label)
            == observed_ledger_counts[label]
            for label in LEDGER_LABELS
        )
        and recorded_ledger_counts.get("all_visible_gaps")
        == sum(observed_ledger_counts.values()),
        "completeness ledger aggregate count drift",
    )
    require(
        audit.get("gap_counts")
        == dict(sorted(observed_gap_counts.items())),
        "completeness gap-kind count drift",
    )
    gap_counts = audit["gap_counts"]
    content = audit.get("content_counts") or {}
    relationships = audit.get("relationship_counts") or {}
    require(
        content.get("parsed_records") == closure.get("top_level_planned")
        and content.get("unique_raw_content_hashes")
        == closure.get("top_level_planned")
        and content.get("unique_parsed_content_hashes")
        == closure.get("top_level_planned"),
        "completeness content/hash partition drift",
    )
    for record_count, missing_gap in (
        ("records_with_titles", "no_titles"),
        ("records_with_summaries", "no_summaries"),
        ("records_with_actions", "no_actions"),
        ("records_with_recorded_votes", "no_recorded_votes"),
        ("records_with_text_versions", "no_text_versions"),
        ("records_with_text_links", "no_text_links"),
    ):
        require(
            content.get(record_count, 0)
            + gap_counts.get(missing_gap, 0)
            == closure.get("top_level_planned"),
            f"completeness record evidence partition drift: {record_count}",
        )
    require(
        content.get("unstructured_relevance_sections", 0)
        == sum(
            gap_counts.get(gap_kind, 0)
            for gap_kind in UNSTRUCTURED_RELEVANCE_SECTIONS.values()
        ),
        "completeness unstructured relevance partition drift",
    )
    required_relationship_counts = (
        "relationship_rows",
        "sponsor_items",
        "cosponsor_items",
        "records_with_sponsors",
        "records_with_cosponsors",
        "sponsor_relationships",
        "original_cosponsor_relationships",
        "later_cosponsor_relationships",
        "withdrawn_relationships",
        "unresolved_role_relationships",
        "relationships_with_bioguide",
        "relationships_missing_bioguide",
        "relationships_missing_full_name",
        "on_behalf_of_sponsor_items",
        "unstructured_role_relationships",
        "repeated_member_relationship_groups",
        "repeated_member_relationship_rows",
        "unique_bioguide_ids",
    )
    require(
        all(
            type(relationships.get(field)) is int
            and relationships[field] >= 0
            for field in required_relationship_counts
        )
        and relationships["relationship_rows"]
        == relationships["sponsor_items"]
        + relationships["cosponsor_items"]
        == relationships["sponsor_relationships"]
        + relationships["original_cosponsor_relationships"]
        + relationships["later_cosponsor_relationships"]
        + relationships["unresolved_role_relationships"]
        and relationships["sponsor_items"]
        == relationships["sponsor_relationships"]
        and relationships["cosponsor_items"]
        == relationships["original_cosponsor_relationships"]
        + relationships["later_cosponsor_relationships"]
        + relationships["unresolved_role_relationships"]
        and relationships["relationship_rows"]
        == relationships["relationships_with_bioguide"]
        + relationships["relationships_missing_bioguide"]
        and relationships["records_with_sponsors"]
        + gap_counts.get("no_sponsors", 0)
        == closure.get("top_level_planned")
        and relationships["records_with_cosponsors"]
        + gap_counts.get("no_cosponsors", 0)
        == closure.get("top_level_planned")
        and relationships["unresolved_role_relationships"]
        == gap_counts.get("relationship_role_unresolved", 0)
        and relationships["relationships_missing_bioguide"]
        == gap_counts.get("relationship_bioguide_missing", 0)
        and relationships["relationships_missing_full_name"]
        == gap_counts.get("relationship_full_name_missing", 0)
        and relationships["on_behalf_of_sponsor_items"]
        == relationships["unstructured_role_relationships"]
        == gap_counts.get("on_behalf_of_sponsor_not_structured", 0)
        and relationships["repeated_member_relationship_groups"]
        == gap_counts.get("repeated_member_relationship_history", 0)
        and relationships["repeated_member_relationship_rows"]
        >= 2 * relationships["repeated_member_relationship_groups"],
        "completeness relationship partition drift",
    )
    require(
        gap_counts.get(
            "nested_amendment_has_no_standalone_bulk_package", 0
        )
        == closure.get("nested_amendments_retained_unresolved"),
        "completeness nested-amendment visibility drift",
    )
    return audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-manifest", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audit, candidate, fields, roles = build_audit(args.worker_manifest)
    path = write_audit(args.out_root, audit, candidate, fields, roles)
    final = validate_audit(path)
    print(
        json.dumps(
            {
                "status": final["status"],
                "audit": str(path),
                "closure": final["closure"],
                "content_counts": final["content_counts"],
                "relationship_counts": final["relationship_counts"],
                "gap_ledger_counts": final["gap_ledger_counts"],
                "network_executed": False,
                "sync_or_promotion_performed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
