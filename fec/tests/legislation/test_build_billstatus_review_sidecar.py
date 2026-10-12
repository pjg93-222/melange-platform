from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from legislation import build_billstatus_review_sidecar as sidecar


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    candidate_file = tmp_path / "advanced_records.ndjson"
    candidates = [
        {
            "canonical_key": "119:hr:1",
            "congress": 119,
            "type": "hr",
            "number": 1,
            "display_number": "H.R. 1",
            "title": "Display candidate title",
            "canonical_url": (
                "https://www.congress.gov/bill/119th-congress/house-bill/1"
            ),
        },
        {
            "canonical_key": "119:hamdt:1",
            "congress": 119,
            "type": "hamdt",
            "number": 1,
            "display_number": "H.Amdt. 1",
            "title": "Retained amendment",
            "canonical_url": (
                "https://www.congress.gov/amendment/119th-congress/"
                "house-amendment/1"
            ),
        },
    ]
    write_ndjson(candidate_file, candidates)
    plan_path = tmp_path / "snapshot_plan.json"
    plan = {
        "schema": "govinfo-billstatus-enrichment-fetch-plan-v1",
        "primary_discovery": "Congress.gov Advanced Search - Legislation",
        "quorum_used": False,
        "inputs": {
            "advanced_search_records": sidecar.file_item(
                candidate_file, record_count=2
            )
        },
        "record_tasks": [{"canonical_key": "119:hr:1"}],
        "unsupported_candidates": [{"canonical_key": "119:hamdt:1"}],
        "candidate_partition": {
            "all": 2,
            "top_level_enrichable": 1,
            "nested_only_amendments": 1,
        },
    }
    write_json(plan_path, plan)

    parsed_path = tmp_path / "parsed.json"
    parsed = {
        "schema": "govinfo-billstatus-enrichment-record-v1",
        "canonical_key": "119:hr:1",
        "identity": {"congress": 119, "type": "hr", "number": 1},
        "titles": [
            {
                "title": "Short Example Act",
                "title_type": "Display Title",
            },
            {
                "title": "To require exact official evidence.",
                "title_type": "Official Title as Introduced",
            },
        ],
        "summaries": [
            {
                "action_date": "2025-01-01",
                "update_date": "2025-01-02T00:00:00Z",
                "version_code": "00",
                "text": "<p>Older <b>official</b> summary.</p>",
            },
            {
                "action_date": "2025-01-03",
                "update_date": "2025-01-04T00:00:00Z",
                "version_code": "01",
                "text": "<p>Latest &amp; specific summary.</p>",
            },
        ],
        "sponsors": [
            {
                "bioguide_id": "A000001",
                "full_name": "Rep. Alpha [D-AA-1]",
                "party": "D",
                "state": "AA",
                "district": "1",
                "sponsorship_date": None,
                "withdrawn_date": None,
                "relationship_status": "current",
                "relationship_role": "Sponsor",
                "role_provenance": "bill/sponsors/item",
                "role_provenance_complete": True,
            }
        ],
        "cosponsors": [
            {
                "bioguide_id": "B000002",
                "full_name": "Rep. Beta [R-BB-2]",
                "party": "R",
                "state": "BB",
                "district": "2",
                "sponsorship_date": "2025-01-01",
                "original_cosponsor": True,
                "withdrawn_date": None,
                "relationship_status": "current",
                "relationship_role": "Original Cosponsor",
                "role_provenance": (
                    "bill/cosponsors/item/isOriginalCosponsor=true"
                ),
                "role_provenance_complete": True,
            },
            {
                "bioguide_id": "C000003",
                "full_name": "Rep. Gamma [D-CC-3]",
                "party": "D",
                "state": "CC",
                "district": "3",
                "sponsorship_date": "2025-02-01",
                "original_cosponsor": False,
                "withdrawn_date": None,
                "relationship_status": "current",
                "relationship_role": "Cosponsor",
                "role_provenance": (
                    "bill/cosponsors/item/isOriginalCosponsor=false"
                ),
                "role_provenance_complete": True,
            },
            {
                "bioguide_id": "D000004",
                "full_name": "Rep. Delta [I-DD-4]",
                "party": "I",
                "state": "DD",
                "district": "4",
                "sponsorship_date": "2025-02-02",
                "original_cosponsor": False,
                "withdrawn_date": "2025-02-03",
                "relationship_status": "withdrawn",
                "relationship_role": "Cosponsor",
                "role_provenance": (
                    "bill/cosponsors/item/isOriginalCosponsor=false"
                ),
                "role_provenance_complete": True,
            },
        ],
        "provenance": {
            "source_url": (
                "https://www.govinfo.gov/bulkdata/BILLSTATUS/119/hr/"
                "BILLSTATUS-119hr1.xml"
            ),
            "source_sha256": "a" * 64,
        },
    }
    write_json(parsed_path, parsed)
    worker_path = tmp_path / "run_manifest.json"
    worker = {
        "schema": "govinfo-billstatus-enrichment-run-v1",
        "status": "COMPLETE",
        "official_source_only": True,
        "production_write_performed": False,
        "sync_or_promotion_performed": False,
        "candidate_creation_performed": False,
        "semantic_exclusion_performed": False,
        "plan": {"path": str(plan_path), "sha256": sidecar.sha256_file(plan_path)},
        "record_receipts": [
            {
                "canonical_key": "119:hr:1",
                "parsed_path": str(parsed_path),
                "parsed_sha256": sidecar.sha256_file(parsed_path),
            }
        ],
    }
    write_json(worker_path, worker)

    candidate_gaps = tmp_path / "candidate_gaps.ndjson"
    field_gaps = tmp_path / "field_gaps.ndjson"
    role_gaps = tmp_path / "role_gaps.ndjson"
    write_ndjson(
        candidate_gaps,
        [
            {
                "canonical_key": "119:hamdt:1",
                "gap": "nested_amendment_has_no_standalone_bulk_package",
            }
        ],
    )
    write_ndjson(
        field_gaps,
        [{"canonical_key": "119:hr:1", "gap": "no_recorded_votes"}],
    )
    write_ndjson(role_gaps, [])
    completeness_path = tmp_path / "completeness_audit.json"
    completeness = {
        "schema": "billstatus-enrichment-completeness-audit-v1",
        "status": "PASS_WITH_VISIBLE_GAPS",
        "production_write_performed": False,
        "sync_or_promotion_performed": False,
        "candidate_creation_performed": False,
        "candidate_exclusion_performed": False,
        "automatic_scoring_performed": False,
        "absence_is_neutral": True,
        "inputs": {
            "worker_manifest": {
                "path": str(worker_path),
                "sha256": sidecar.sha256_file(worker_path),
            },
            "snapshot_plan": {
                "path": str(plan_path),
                "sha256": sidecar.sha256_file(plan_path),
            },
        },
        "closure": {
            "candidate_partition_closed": True,
            "planned_key_set_equals_receipt_key_set": True,
            "all_raw_hashes_validated": True,
            "all_parsed_hashes_validated": True,
            "all_xml_identities_validated": True,
        },
        "relationship_counts": {
            "sponsor_relationships": 1,
            "original_cosponsor_relationships": 1,
            "later_cosponsor_relationships": 2,
            "withdrawn_relationships": 1,
            "unresolved_role_relationships": 0,
        },
        "gap_ledger_counts": {"all_visible_gaps": 2},
        "outputs": {
            "candidate_visibility_gaps": sidecar.file_item(
                candidate_gaps, record_count=1
            ),
            "field_completeness_gaps": sidecar.file_item(
                field_gaps, record_count=1
            ),
            "role_provenance_gaps": sidecar.file_item(
                role_gaps, record_count=0
            ),
        },
    }
    write_json(completeness_path, completeness)
    return plan_path, worker_path, completeness_path


class BillstatusReviewSidecarTest(unittest.TestCase):
    def test_builds_lossless_review_only_sidecar_and_reuses_it(self) -> None:
        with TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            plan, worker, completeness = fixture(tmp_path)
            pointer = tmp_path / "current.json"
            out, manifest, created = sidecar.build_sidecar(
                plan_path=plan,
                worker_path=worker,
                completeness_path=completeness,
                out_root=tmp_path / "sidecars",
                pointer_path=pointer,
            )
            self.assertTrue(created)
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["selected_record_count"], 2)
            self.assertEqual(manifest["top_level_metadata_count"], 1)
            self.assertEqual(manifest["edge_count"], 4)
            self.assertEqual(manifest["unresolved_measure_count"], 1)
            self.assertEqual(manifest["visible_gap_count"], 2)
            self.assertFalse(manifest["acceptance_passed"])
            self.assertFalse(manifest["all_relationships_reconciled"])
            self.assertFalse(manifest["candidate_creation_performed"])
            self.assertFalse(manifest["semantic_exclusion_performed"])
            self.assertFalse(manifest["ratification_authority"])
            self.assertFalse(manifest["scoring_authority"])

            metadata = list(
                sidecar.iter_ndjson(
                    out / manifest["measure_metadata_file"]
                )
            )
            self.assertEqual(
                metadata[0]["official_title"],
                "To require exact official evidence.",
            )
            self.assertEqual(
                metadata[0]["summary"], "Latest & specific summary."
            )
            self.assertEqual(
                metadata[0]["relationship_role_counts"],
                {
                    "Sponsor": 1,
                    "Original Cosponsor": 1,
                    "Later Cosponsor": 2,
                    "withdrawn": 1,
                    "unresolved_role": 0,
                },
            )
            edges = list(
                sidecar.iter_ndjson(out / manifest["edges_file"])
            )
            self.assertEqual(
                {row["explicit_role"] for row in edges},
                {"Sponsor", "Original Cosponsor", "Later Cosponsor"},
            )
            self.assertTrue(
                all(row["scoring_authority"] is False for row in edges)
            )
            unresolved = list(
                sidecar.iter_ndjson(
                    out / manifest["verification_ledger_file"]
                )
            )
            self.assertEqual(
                unresolved[0]["canonical_key"], "119:hamdt:1"
            )
            self.assertTrue(unresolved[0]["absence_is_neutral"])
            gaps = list(
                sidecar.iter_ndjson(
                    out / manifest["source_gap_ledger_file"]
                )
            )
            self.assertEqual(len(gaps), 2)
            self.assertTrue(
                all(row["candidate_retained"] is True for row in gaps)
            )
            self.assertEqual(
                sidecar.read_json(pointer)["sidecar_id"],
                manifest["sidecar_id"],
            )

            out_again, manifest_again, created_again = (
                sidecar.build_sidecar(
                    plan_path=plan,
                    worker_path=worker,
                    completeness_path=completeness,
                    out_root=tmp_path / "sidecars",
                    pointer_path=pointer,
                )
            )
            self.assertEqual(out_again, out)
            self.assertEqual(
                manifest_again["sidecar_id"], manifest["sidecar_id"]
            )
            self.assertFalse(created_again)

    def test_rejects_tampered_bound_gap_ledger(self) -> None:
        with TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            plan, worker, completeness = fixture(tmp_path)
            audit = sidecar.read_json(completeness)
            gap_path = Path(
                audit["outputs"]["field_completeness_gaps"]["path"]
            )
            gap_path.write_text('{"tampered":true}\n', encoding="utf-8")
            with self.assertRaisesRegex(
                sidecar.ContractError, "SHA-256 mismatch"
            ):
                sidecar.build_sidecar(
                    plan_path=plan,
                    worker_path=worker,
                    completeness_path=completeness,
                    out_root=tmp_path / "sidecars",
                )

    def test_rejects_worker_with_decision_or_candidate_authority(
        self,
    ) -> None:
        with TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            plan, worker, completeness = fixture(tmp_path)
            worker_value = sidecar.read_json(worker)
            worker_value["candidate_creation_performed"] = True
            write_json(worker, worker_value)
            completeness_value = sidecar.read_json(completeness)
            completeness_value["inputs"]["worker_manifest"]["sha256"] = (
                sidecar.sha256_file(worker)
            )
            write_json(completeness, completeness_value)
            with self.assertRaisesRegex(sidecar.ContractError, "staged-only"):
                sidecar.build_sidecar(
                    plan_path=plan,
                    worker_path=worker,
                    completeness_path=completeness,
                    out_root=tmp_path / "sidecars",
                )


if __name__ == "__main__":
    unittest.main()
