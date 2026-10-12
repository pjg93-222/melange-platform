import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


FEC_DIR = Path(__file__).resolve().parents[2]
if str(FEC_DIR) not in sys.path:
    sys.path.insert(0, str(FEC_DIR))

import audit.audit_congress_collector_resume_continuity as audit


class CollectorResumeContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "queries").mkdir()
        (self.root / "raw" / "pages" / "q1").mkdir(parents=True)
        self.records = [
            {"canonical_key": "116:hr:1", "congress": 116, "type": "hr"},
            {"canonical_key": "119:s:2", "congress": 119, "type": "s"},
        ]
        self.records_sha = audit.javascript_json_sha256(self.records)
        self._write_query()
        self.baseline = self.root / "manifest.partial.1.json"
        self.current = self.root / "manifest.partial.2.json"
        self._write_manifests()
        self.partition_audit = self._write_partition_audit()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _json(self, path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def _write_query(self) -> None:
        page_rel = "raw/pages/q1/page_001.json"
        page = {
            "query_id": "q1",
            "page_number": 1,
            "result_range": {"start": 1, "end": 2, "total": 2},
            "records_sha256": self.records_sha,
            "validation": {"range_matches": True},
            "records": self.records,
        }
        self._json(self.root / page_rel, page)
        query = {
            "schema": "congress-advanced-search-query-v1",
            "completed_at": "2026-07-26T00:00:00Z",
            "query_id": "q1",
            "result_total": 2,
            "unique_record_count": 2,
            "page_count": 1,
            "counts_by_congress": {"116": 1, "119": 1},
            "counts_by_type": {"hr": 1, "s": 1},
            "pages": [
                {
                    "file": page_rel,
                    "page_number": 1,
                    "result_range": {"start": 1, "end": 2, "total": 2},
                    "record_count": 2,
                    "records_sha256": self.records_sha,
                }
            ],
            "records_sha256": self.records_sha,
            "validation": {
                "exact_result_total": True,
                "unique_result_total": True,
                "every_page_exact": True,
            },
            "records": self.records,
        }
        self._json(self.root / "queries" / "q1.json", query)
        self.query = query

    def _manifest(self, started_at: str, receipt: str) -> dict:
        entry = {
            "query_id": "q1",
            "query_file": "queries/q1.json",
            **{
                field: self.query[field]
                for field in audit.QUERY_SEMANTIC_FIELDS
                if field != "query_id"
            },
        }
        return {
            "schema": "acu-congress-advanced-pages-manifest-v1",
            "status": "failed",
            "started_at": started_at,
            "primary_instrument": "Congress.gov Advanced Search - Legislation",
            "quorum_used": False,
            "registry_file": "registry.json",
            "registry_sha256": "a" * 64,
            "collection_contract": {"lossless": True},
            "page_contract": {"exact": True},
            "selected_query_ids": ["q1", "q2"],
            "queries": [entry],
            "automatic_verification_timeout": {
                "classification": "automatic_verification_timeout",
                "receipt_file": receipt,
                "receipt_sha256": hashlib.sha256(receipt.encode()).hexdigest(),
                "interaction_performed": False,
                "captcha_bypass_attempted": False,
                "collection_stopped_without_result_acceptance": True,
            },
        }

    def _write_manifests(self) -> None:
        (self.root / "timeout-1.json").write_text(
            "timeout-1.json", encoding="utf-8"
        )
        (self.root / "timeout-2.json").write_text(
            "timeout-2.json", encoding="utf-8"
        )
        self._json(
            self.baseline,
            self._manifest("2026-07-26T00:00:00Z", "timeout-1.json"),
        )
        self._json(
            self.current,
            self._manifest("2026-07-26T01:00:00Z", "timeout-2.json"),
        )

    def _write_ndjson(self, path: Path, rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )

    def _write_partition_audit(self) -> Path:
        review = self.root / "review"
        review.mkdir()
        manifest = review / "manifest.json"
        self._json(manifest, {"membership_count": 2})
        ledger = review / "membership_review_ledger.ndjson"
        included = review / "included_memberships.ndjson"
        excluded = review / "false_positive_exclusions.ndjson"
        unresolved = review / "unresolved_memberships.ndjson"
        rows = [
            {"canonical_key": "116:hr:1", "query_id": "q1"},
            {"canonical_key": "119:s:2", "query_id": "q1"},
        ]
        self._write_ndjson(ledger, rows)
        self._write_ndjson(included, rows[:1])
        self._write_ndjson(excluded, [])
        self._write_ndjson(unresolved, rows[1:])
        key_sha = "b" * 64
        report = {
            "schema": "acu-candidate-membership-review-partition-audit-v1",
            "status": "PASS",
            "blocking_issue_count": 0,
            "source_bindings": {
                "manifest_file": str(manifest),
                "manifest_file_sha256": audit.sha256_file(manifest),
                "ledger_file": str(ledger),
                "ledger_file_sha256": audit.sha256_file(ledger),
                "ledger_line_count": 2,
                "ledger_stable_membership_key_set_sha256": key_sha,
            },
            "partition_files": {
                "included": {
                    "file": str(included),
                    "file_sha256": audit.sha256_file(included),
                    "line_count": 1,
                },
                "excluded": {
                    "file": str(excluded),
                    "file_sha256": audit.sha256_file(excluded),
                    "line_count": 0,
                },
                "unresolved": {
                    "file": str(unresolved),
                    "file_sha256": audit.sha256_file(unresolved),
                    "line_count": 1,
                },
            },
            "partition_exactness": {
                "ledger_and_partition_key_hashes_equal": True,
                "partition_stable_membership_key_union_sha256": key_sha,
            },
            "ledger_disposition_counts": {
                "include": 1,
                "exclude_false_positive": 0,
                "unresolved": 1,
            },
        }
        report["audit_payload_sha256"] = audit.canonical_json_sha256(report)
        audit_path = self.root / "partition_audit.json"
        self._json(audit_path, report)
        audit_path.with_suffix(".json.sha256").write_text(
            f"{audit.sha256_file(audit_path)}  {audit_path.name}\n",
            encoding="ascii",
        )
        return audit_path

    def test_run_metadata_rematerialization_passes_without_record_drift(self) -> None:
        source_paths = [
            self.baseline,
            self.current,
            self.root / "queries" / "q1.json",
            self.partition_audit,
        ]
        before = {path: audit.sha256_file(path) for path in source_paths}
        report = audit.audit_continuity(
            self.baseline,
            self.current,
            self.partition_audit,
            expected_query_count=1,
        )
        after = {path: audit.sha256_file(path) for path in source_paths}
        self.assertEqual("PASS", report["status"])
        self.assertEqual(before, after)
        self.assertFalse(
            report["manifest_continuity"]["semantic_record_drift_detected"]
        )
        self.assertEqual(
            "run_timestamp_timeout_receipt_rematerialization_only",
            report["manifest_continuity"]["difference_classification"],
        )
        self.assertTrue(
            report["current_query_artifact_validation"][
                "all_referenced_query_and_page_files_validate"
            ]
        )
        self.assertTrue(
            report["candidate_review_partition_binding"][
                "all_review_files_unchanged_and_hash_bound"
            ]
        )
        self.assertTrue(
            report["current_timeout_receipt_binding"][
                "immutable_receipt_hash_valid"
            ]
        )

    def test_timeout_receipt_tamper_fails_closed(self) -> None:
        (self.root / "timeout-2.json").write_text(
            "tampered", encoding="utf-8"
        )
        report = audit.audit_continuity(
            self.baseline,
            self.current,
            self.partition_audit,
            expected_query_count=1,
        )
        self.assertEqual("FAIL", report["status"])
        self.assertFalse(
            report["current_timeout_receipt_binding"][
                "immutable_receipt_hash_valid"
            ]
        )

    def test_semantic_record_hash_drift_fails_closed(self) -> None:
        current = json.loads(self.current.read_text(encoding="utf-8"))
        current["queries"][0]["records_sha256"] = "0" * 64
        self._json(self.current, current)
        report = audit.audit_continuity(
            self.baseline,
            self.current,
            self.partition_audit,
            expected_query_count=1,
        )
        self.assertEqual("FAIL", report["status"])
        self.assertTrue(
            report["manifest_continuity"]["semantic_record_drift_detected"]
        )
        self.assertEqual(
            "semantic_record_drift",
            report["manifest_continuity"]["difference_classification"],
        )

    def test_query_or_partition_tamper_fails_closed(self) -> None:
        query = copy.deepcopy(self.query)
        query["records"][0]["type"] = "s"
        self._json(self.root / "queries" / "q1.json", query)
        included = self.root / "review" / "included_memberships.ndjson"
        included.write_text("", encoding="utf-8")
        report = audit.audit_continuity(
            self.baseline,
            self.current,
            self.partition_audit,
            expected_query_count=1,
        )
        self.assertEqual("FAIL", report["status"])
        self.assertGreater(
            report["current_query_artifact_validation"]["error_count"], 0
        )
        self.assertFalse(
            report["candidate_review_partition_binding"][
                "all_review_files_unchanged_and_hash_bound"
            ]
        )


if __name__ == "__main__":
    unittest.main()
