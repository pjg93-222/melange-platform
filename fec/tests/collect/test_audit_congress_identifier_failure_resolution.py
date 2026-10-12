import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

import audit.audit_congress_identifier_failure_resolution as audit


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fixture_run(tmp_path: Path) -> tuple[Path, str]:
    run = tmp_path / "run"
    query_ids = ["query_one", "query_two"]
    query_entries = []
    for query_id in query_ids:
        record = {
            "canonical_key": f"119:hr:{1 if query_id == 'query_one' else 2}",
            "source_query_id": query_id,
        }
        records = [record]
        records_hash = audit.record_hash(records)
        page_file = f"raw/pages/{query_id}/page_001.json"
        query_file = f"queries/{query_id}.json"
        result_range = {
            "start": 1,
            "end": 1,
            "total": 1,
            "header": "Search Results 1-1 of 1",
        }
        validation = {
            "exact_result_total": True,
            "unique_result_total": True,
            "every_page_exact": True,
            "single_result_redirect_proven": True,
        }
        metadata = {
            "query_id": query_id,
            "label": query_id,
            "scoring_category": False,
            "record_kind": "identifier_probe",
            "parent_category_id": "fixture",
            "parent_category_label": "Fixture",
            "identifier_text": query_id,
            "title_alias": None,
            "ambiguous_short_act_alias": False,
            "membership_non_dispositive": False,
            "source_paragraph": 1,
        }
        page_descriptor = {
            "file": page_file,
            "page_number": 1,
            "result_range": result_range,
            "record_count": 1,
            "records_sha256": records_hash,
        }
        write_json(
            run / page_file,
            {
                "schema": audit.EXPECTED_PAGE_SCHEMA,
                "primary_instrument": audit.PRIMARY_INSTRUMENT,
                "query_id": query_id,
                "page_number": 1,
                "result_range": result_range,
                "result_total": 1,
                "records_sha256": records_hash,
                "validation": {"every_page_exact": True},
                "records": records,
            },
        )
        artifact = {
            "schema": audit.EXPECTED_QUERY_SCHEMA,
            "primary_instrument": audit.PRIMARY_INSTRUMENT,
            **metadata,
            "result_total": 1,
            "unique_record_count": 1,
            "page_count": 1,
            "counts_by_congress": {"119": 1},
            "counts_by_type": {"hr": 1},
            "records_sha256": records_hash,
            "pages": [page_descriptor],
            "validation": validation,
            "records": records,
        }
        write_json(run / query_file, artifact)
        query_entries.append(
            {
                **metadata,
                "query_file": query_file,
                "result_total": 1,
                "unique_record_count": 1,
                "page_count": 1,
                "counts_by_congress": {"119": 1},
                "counts_by_type": {"hr": 1},
                "records_sha256": records_hash,
                "validation": validation,
            }
        )
    write_json(
        run / "manifest.json",
        {
            "schema": audit.EXPECTED_MANIFEST_SCHEMA,
            "status": "complete",
            "primary_instrument": audit.PRIMARY_INSTRUMENT,
            "quorum_used": False,
            "selected_query_ids": query_ids,
            "queries": query_entries,
        },
    )
    write_json(
        run / "progress.json",
        {
            "schema": audit.EXPECTED_PROGRESS_SCHEMA,
            "status": "complete",
            "completed_queries": 2,
            "total_queries": 2,
        },
    )
    failure = {
        "schema": audit.EXPECTED_FAILURE_SCHEMA,
        "failed_at": "2026-07-26T00:00:00Z",
        "query_id": "query_one",
        "message": "historical fixture failure",
    }
    receipt_hash = hashlib.sha256(
        audit.javascript_json_bytes(failure)
    ).hexdigest()
    write_json(
        run / "failure_receipts" / f"{receipt_hash}.json",
        {**failure, "receipt_sha256": receipt_hash},
    )
    return run, receipt_hash


class IdentifierFailureResolutionTests(unittest.TestCase):
    def test_resolves_only_from_complete_hash_consistent_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run, _ = fixture_run(Path(directory))
            receipt = audit.audit_failure_resolution(run, expected_total=2)
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(
                receipt["counts"],
                {
                    "historical_failure_receipt_count": 1,
                    "unique_failed_query_count": 1,
                    "resolved_failure_receipt_count": 1,
                    "resolved_query_count": 1,
                    "unresolved_failure_receipt_count": 0,
                    "unresolved_query_count": 0,
                },
            )
            self.assertFalse(
                receipt["policy"]["scoring_sync_promotion_authorized"]
            )

    def test_incomplete_progress_blocks_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run, _ = fixture_run(Path(directory))
            progress = json.loads(
                (run / "progress.json").read_text(encoding="utf-8")
            )
            progress["completed_queries"] = 1
            progress["status"] = "running"
            write_json(run / "progress.json", progress)
            receipt = audit.audit_failure_resolution(run, expected_total=2)
            self.assertEqual(receipt["status"], "FAIL")
            self.assertEqual(
                receipt["counts"]["unresolved_failure_receipt_count"], 1
            )

    def test_missing_query_artifact_blocks_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run, _ = fixture_run(Path(directory))
            (run / "queries" / "query_one.json").unlink()
            receipt = audit.audit_failure_resolution(run, expected_total=2)
            self.assertEqual(receipt["status"], "FAIL")
            self.assertIn(
                "query artifact missing",
                " ".join(receipt["unresolved_queries"][0]["reasons"]),
            )

    def test_tampered_failure_receipt_remains_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run, receipt_hash = fixture_run(Path(directory))
            failure_path = (
                run / "failure_receipts" / f"{receipt_hash}.json"
            )
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            failure["message"] = "tampered"
            write_json(failure_path, failure)
            receipt = audit.audit_failure_resolution(run, expected_total=2)
            self.assertEqual(receipt["status"], "FAIL")
            self.assertEqual(
                receipt["counts"]["unresolved_failure_receipt_count"], 1
            )
            self.assertIn(
                "content_matches_declared",
                " ".join(
                    receipt["unresolved_failure_receipts"][0]["reasons"]
                ),
            )


if __name__ == "__main__":
    unittest.main()
