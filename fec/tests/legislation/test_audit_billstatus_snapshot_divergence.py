from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

import audit.audit_billstatus_snapshot_divergence as AUDIT
from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_lossless_divergence_partition_and_evidence(tmp_path: Path) -> None:
    run = tmp_path / "run"
    query_dir = run / "queries"
    query_dir.mkdir(parents=True)
    proven_source = {
        "canonical_key": "119:hr:1",
        "congress": 119,
        "type": "hr",
        "number": 1,
    }
    record_hash = BILLSTATUS.stable_json_sha256([proven_source])
    query = {
        "schema": "congress-advanced-search-query-v1",
        "query_id": "q1",
        "registry_sha256": "a" * 64,
        "record_kind": "category",
        "scoring_category": True,
        "result_total": 1,
        "unique_record_count": 1,
        "records_sha256": record_hash,
        "validation": {
            "exact_result_total": True,
            "unique_result_total": True,
            "every_page_exact": True,
        },
        "records": [proven_source],
    }
    (query_dir / "q1.json").write_text(json.dumps(query), encoding="utf-8")
    old_query = {
        "schema": "congress-advanced-search-query-v1",
        "query_id": "old_q",
        "registry_sha256": "a" * 64,
        "record_kind": "category",
        "scoring_category": True,
        "result_total": 0,
        "unique_record_count": 0,
        "records_sha256": BILLSTATUS.stable_json_sha256([]),
        "validation": {
            "exact_result_total": True,
            "unique_result_total": True,
            "every_page_exact": True,
        },
        "records": [],
    }
    (query_dir / "old_q.json").write_text(
        json.dumps(old_query), encoding="utf-8"
    )
    strict = run / "manifest.partial.json"
    strict.write_text(
        json.dumps(
            {
                "primary_instrument": BILLSTATUS.PRIMARY_DISCOVERY,
                "quorum_used": False,
                "registry_sha256": "a" * 64,
                "selected_query_ids": ["q1", "old_q"],
                "queries": [
                    {
                        "query_id": "q1",
                        "query_file": "queries/q1.json",
                        "result_total": 1,
                        "unique_record_count": 1,
                        "records_sha256": record_hash,
                    },
                    {
                        "query_id": "old_q",
                        "query_file": "queries/old_q.json",
                        "result_total": 0,
                        "unique_record_count": 0,
                        "records_sha256": BILLSTATUS.stable_json_sha256([]),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    rows = [
        {
            **proven_source,
            "matched_scoring_categories": ["q1"],
            "matched_monitor_categories": [],
            "matched_coverage_probes": [],
        },
        {
            "canonical_key": "119:hr:2",
            "congress": 119,
            "type": "hr",
            "number": 2,
            "matched_scoring_categories": ["old_q"],
            "matched_monitor_categories": [],
            "matched_coverage_probes": [],
        },
    ]
    records = run / "records.ndjson"
    write_ndjson(records, rows)
    memberships = run / "memberships.ndjson"
    write_ndjson(
        memberships,
        [
            {
                "canonical_key": "119:hr:2",
                "query_id": "old_q",
                "record_kind": "category",
                "scoring_category": True,
                "source_page": 1,
                "source_rank": 2,
            }
        ],
    )
    review = run / "review.ndjson"
    write_ndjson(
        review,
        [
            {
                "canonical_key": "119:hr:2",
                "query_id": "old_q",
                "disposition": "unresolved",
                "candidate_retained_in_discovery_corpus": True,
            }
        ],
    )
    false_positives = run / "false_positive_exclusions.ndjson"
    write_ndjson(false_positives, [])
    aggregate = run / "manifest.json"
    membership_count, membership_hash = (
        AUDIT.ndjson_logical_array_receipt(memberships)
    )
    aggregate.write_text(
        json.dumps(
            {
                "status": "complete",
                "primary_instrument": BILLSTATUS.PRIMARY_DISCOVERY,
                "quorum_used": False,
                "registry_sha256": "0" * 64,
                "selected_query_ids": ["old_q"],
                "queries": [
                    {
                        "query_id": "old_q",
                        "query_file": "queries/old_q.json",
                        "result_total": 1,
                        "unique_record_count": 1,
                        "records_sha256": "1" * 64,
                    }
                ],
                "union": {
                    "records_file": records.name,
                    "unique_record_count": 2,
                    "records_sha256": BILLSTATUS.stable_json_sha256(rows),
                    "memberships_file": memberships.name,
                    "membership_count": membership_count,
                    "memberships_sha256": membership_hash,
                }
            }
        ),
        encoding="utf-8",
    )
    manifest, proven, unproven = AUDIT.build_audit(
        records_path=records,
        strict_manifest_path=strict,
        memberships_path=memberships,
        review_ledger_path=review,
        false_positive_exclusions_path=false_positives,
        aggregate_manifest_path=aggregate,
    )
    assert manifest["partition"] == {
        "all": 2,
        "proven_by_selected_strict_query_artifacts": 1,
        "unproven_by_selected_strict_query_artifacts": 1,
        "historical_aggregate_proven_and_retained": 1,
        "unresolved_without_historical_aggregate_receipt": 0,
        "closed": True,
    }
    assert manifest["status"] == (
        "CUMULATIVE_PROVEN_WITH_CURRENT_QUERY_DIVERGENCE"
    )
    assert proven[0]["canonical_key"] == "119:hr:1"
    assert unproven[0]["canonical_key"] == "119:hr:2"
    assert unproven[0]["aggregate_line"] == 2
    assert len(unproven[0]["aggregate_line_sha256"]) == 64
    assert unproven[0]["evidence_summary"]["membership_found"] is True
    assert unproven[0]["evidence_summary"]["membership_review_found"] is True
    assert (
        unproven[0]["aggregate_manifest_union_receipt_applies"] is True
    )
    assert (
        unproven[0]["aggregate_manifest_membership_receipt_applies"] is True
    )
    assert unproven[0]["disposition"] == (
        "historical_snapshot_proven_retained_current_query_"
        "divergence_unresolved"
    )
    assert unproven[0]["candidate_retained"] is True
    assert unproven[0]["semantic_exclusion_authorized"] is False
    provenance = unproven[0]["membership_evidence"][0][
        "membership_query_provenance"
    ]
    assert provenance["historical_membership_only"] is True
    assert provenance["current_membership_present"] is False
    assert (
        provenance["historical_query_version"]["registry_sha256"]
        == "0" * 64
    )
    gate = manifest["historical_membership_acceptance_gate"]
    assert gate["passed"] is True
    assert gate["membership_count"] == 1
    assert gate["unresolved_non_scoring_count"] == 1
    assert gate["official_exclusion_non_scoring_count"] == 0
    assert gate["officially_ratified_scoring_count"] == 0
    out = AUDIT.write_audit(tmp_path / "audit", manifest, proven, unproven)
    final = json.loads(out.read_text(encoding="utf-8"))
    assert final["outputs"]["proven_candidates"]["record_count"] == 1
    assert final["outputs"]["unproven_candidates"]["record_count"] == 1
    acceptance_receipt = final["outputs"][
        "historical_membership_acceptance"
    ]
    assert acceptance_receipt["record_count"] == 1
    acceptance_rows = [
        json.loads(line)
        for line in Path(acceptance_receipt["path"])
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert acceptance_rows[0]["acceptance_state"] == (
        "historical_visible_unresolved_non_scoring"
    )
    assert acceptance_rows[0]["current_scoring_evidence_eligible"] is False


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(
        prefix="billstatus-divergence-test-"
    ) as directory:
        test_lossless_divergence_partition_and_evidence(Path(directory))
    print("1 BILLSTATUS divergence test passed")
