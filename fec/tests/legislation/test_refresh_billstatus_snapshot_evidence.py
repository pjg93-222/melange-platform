from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

from legislation import refresh_billstatus_snapshot_evidence as REFRESH
from legislation import run_legislation_presync_audit as PRESYNC
from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def fixture(root: Path) -> dict[str, Path | tuple[Path, ...]]:
    run = root / "run"
    query_dir = run / "queries"
    query_dir.mkdir(parents=True)
    source = {
        "canonical_key": "119:hr:1",
        "congress": 119,
        "type": "hr",
        "number": 1,
    }
    record_hash = BILLSTATUS.stable_json_sha256([source])
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
        "records": [source],
    }
    (query_dir / "q1.json").write_text(json.dumps(query), encoding="utf-8")
    strict = run / "manifest.partial.json"
    strict.write_text(
        json.dumps(
            {
                "primary_instrument": BILLSTATUS.PRIMARY_DISCOVERY,
                "quorum_used": False,
                "registry_sha256": "a" * 64,
                "selected_query_ids": ["q1"],
                "queries": [
                    {
                        "query_id": "q1",
                        "query_file": "queries/q1.json",
                        "result_total": 1,
                        "unique_record_count": 1,
                        "records_sha256": record_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    records = run / "records.ndjson"
    aggregate_rows = [
        {
            **source,
            "matched_scoring_categories": ["q1"],
            "matched_monitor_categories": [],
            "matched_coverage_probes": [],
        }
    ]
    write_ndjson(records, aggregate_rows)
    memberships = run / "memberships.ndjson"
    membership_rows = [
        {
            "canonical_key": "119:hr:1",
            "query_id": "q1",
            "record_kind": "category",
            "scoring_category": True,
            "source_page": 1,
            "source_rank": 1,
        }
    ]
    write_ndjson(memberships, membership_rows)
    cumulative = run / "manifest.json"
    cumulative.write_text(
        json.dumps(
            {
                "status": "complete",
                "primary_instrument": BILLSTATUS.PRIMARY_DISCOVERY,
                "quorum_used": False,
                "registry_sha256": "a" * 64,
                "selected_query_ids": ["q1"],
                "queries": [
                    {
                        "query_id": "q1",
                        "query_file": "queries/q1.json",
                        "result_total": 1,
                        "unique_record_count": 1,
                        "records_sha256": record_hash,
                    }
                ],
                "union": {
                    "records_file": records.name,
                    "unique_record_count": 1,
                    "records_sha256": BILLSTATUS.stable_json_sha256(
                        aggregate_rows
                    ),
                    "memberships_file": memberships.name,
                    "membership_count": 1,
                    "memberships_sha256": BILLSTATUS.stable_json_sha256(
                        membership_rows
                    ),
                }
            }
        ),
        encoding="utf-8",
    )
    exclusions = run / "exclusions.ndjson"
    false_positives = run / "false_positive_exclusions.ndjson"
    review = run / "review.ndjson"
    gaps = run / "gaps.ndjson"
    for path in (exclusions, false_positives, review, gaps):
        path.write_text("", encoding="utf-8")
    registry = run / "registry.json"
    registry.write_text("{}\n", encoding="utf-8")
    return {
        "snapshot_root": root / "audits" / "snapshots",
        "strict_manifest_path": strict,
        "records_path": records,
        "memberships_path": memberships,
        "exclusion_ledger_path": exclusions,
        "exclusion_provenance_paths": (false_positives,),
        "cumulative_manifest_path": cumulative,
        "registry_path": registry,
        "review_ledger_path": review,
        "gap_ledger_path": gaps,
    }


def test_idempotent_rollover_and_tamper_failure(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    original_audit = REFRESH.SPECIFICITY.audit

    def fake_specificity(
        registry_path: Path,
        *,
        selected_manifest_path: Path | None = None,
    ) -> dict:
        assert selected_manifest_path is not None
        return {
            "schema": "legislation-query-specificity-audit-v1",
            "status": "PASS",
            "quorum_used": False,
            "semantic_exclusions_performed": False,
            "inputs": {
                "registry": REFRESH.SPECIFICITY.file_receipt(registry_path),
                "selected_strict_manifest": (
                    REFRESH.SPECIFICITY.file_receipt(
                        selected_manifest_path
                    )
                ),
            },
        }

    REFRESH.SPECIFICITY.audit = fake_specificity
    first = REFRESH.ensure_snapshot_bundle(
        **arguments, enforce_workspace_audits=False
    )
    assert first["created"] is True
    first_plan = Path(first["snapshot_plan"])
    first_hash = BILLSTATUS.sha256_file(first_plan)

    repeated = REFRESH.ensure_snapshot_bundle(
        **arguments, enforce_workspace_audits=False
    )
    assert repeated["created"] is False
    assert repeated["bundle_id"] == first["bundle_id"]
    assert BILLSTATUS.sha256_file(first_plan) == first_hash

    strict = arguments["strict_manifest_path"]
    assert isinstance(strict, Path)
    value = json.loads(strict.read_text(encoding="utf-8"))
    value["receipt_generation"] = 2
    strict.write_text(json.dumps(value), encoding="utf-8")
    rolled = REFRESH.ensure_snapshot_bundle(
        **arguments, enforce_workspace_audits=False
    )
    assert rolled["created"] is True
    assert rolled["bundle_id"] != first["bundle_id"]
    assert first_plan.is_file()
    assert BILLSTATUS.sha256_file(first_plan) == first_hash

    rolled_plan = Path(rolled["snapshot_plan"])
    rolled_plan.write_text(
        rolled_plan.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )
    try:
        REFRESH.ensure_snapshot_bundle(
            **arguments, enforce_workspace_audits=False
        )
    except REFRESH.RefreshError as error:
        assert "receipt drift" in str(error)
    else:
        raise AssertionError("tampered immutable bundle validated")
    REFRESH.SPECIFICITY.audit = original_audit


def test_semantic_rollover_binding_is_exact_and_tamper_closed(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    old_plan_value = BILLSTATUS.build_fetch_plan(
        arguments["records_path"],
        arguments["exclusion_ledger_path"],
        arguments["exclusion_provenance_paths"],
        arguments["strict_manifest_path"],
        arguments["cumulative_manifest_path"],
        arguments["memberships_path"],
    )
    old_plan = tmp_path / "audit" / "old_snapshot_plan.json"
    old_plan.parent.mkdir(parents=True)
    old_plan.write_text(json.dumps(old_plan_value), encoding="utf-8")

    strict = arguments["strict_manifest_path"]
    assert isinstance(strict, Path)
    strict_value = json.loads(strict.read_text(encoding="utf-8"))
    strict_value["receipt_generation"] = 2
    new_strict = strict.with_name("manifest.partial.2.json")
    new_strict.write_text(json.dumps(strict_value), encoding="utf-8")
    new_plan_value = BILLSTATUS.build_fetch_plan(
        arguments["records_path"],
        arguments["exclusion_ledger_path"],
        arguments["exclusion_provenance_paths"],
        new_strict,
        arguments["cumulative_manifest_path"],
        arguments["memberships_path"],
    )
    new_plan = tmp_path / "audit" / "new_snapshot_plan.json"
    new_plan.write_text(json.dumps(new_plan_value), encoding="utf-8")

    binding = PRESYNC.build_billstatus_semantic_rollover_binding(
        current_plan_path=new_plan,
        reused_plan_path=old_plan,
    )
    assert binding["status"] == "PASS"
    assert binding["mode"] == "SEMANTIC_EQUIVALENCE"
    assert binding["physical_receipts_equal"] is False
    identity = binding["semantic_identity"]
    assert identity["completed_query_count"] == 1
    assert identity["query_logical_receipts"] == [
        {
            "query_id": "q1",
            "records_sha256": (
                new_plan_value["snapshot_contract"]
                ["strict_manifest_query_union"]["query_artifacts"][0]
                ["records_sha256"]
            ),
            "record_count": 1,
        }
    ]
    assert (
        identity["candidate_identity"]["canonical_keys_sha256"]
        == new_plan_value["snapshot_contract"]
        ["strict_manifest_query_union"]["canonical_keys_sha256"]
    )
    assert binding["authoritative_for_scoring"] is False
    assert binding["authoritative_for_sync_or_promotion"] is False

    tampered = json.loads(new_plan.read_text(encoding="utf-8"))
    tampered["snapshot_contract"]["strict_manifest_query_union"][
        "canonical_keys_sha256"
    ] = "0" * 64
    new_plan.write_text(json.dumps(tampered), encoding="utf-8")
    try:
        PRESYNC.build_billstatus_semantic_rollover_binding(
            current_plan_path=new_plan,
            reused_plan_path=old_plan,
        )
    except (PRESYNC.PresyncError, BILLSTATUS.ContractError):
        pass
    else:
        raise AssertionError("tampered candidate identity rolled over")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(
        prefix="billstatus-refresh-test-"
    ) as directory:
        test_idempotent_rollover_and_tamper_failure(Path(directory))
        test_semantic_rollover_binding_is_exact_and_tamper_closed(
            Path(directory) / "semantic"
        )
    print("2 BILLSTATUS refresh tests passed")
