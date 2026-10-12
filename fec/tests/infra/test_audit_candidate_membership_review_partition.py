import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


FEC_DIR = Path(__file__).resolve().parents[2]
if str(FEC_DIR) not in sys.path:
    sys.path.insert(0, str(FEC_DIR))

import audit.audit_candidate_membership_review_partition as audit


def base_row(key: str, query_id: str, disposition: str) -> dict:
    return {
        "canonical_key": key,
        "query_id": query_id,
        "disposition": disposition,
        "candidate_retained_in_discovery_corpus": True,
        "article_absence_is_never_exclusion_evidence": True,
    }


def official_exclusion() -> dict:
    row = base_row("117:hr:1", "exam", "exclude_false_positive")
    excerpt = "This bill concerns an unrelated subject."
    row.update(
        {
            "decision_source": "explicit_review_decision",
            "reason": "Official provisions establish a different subject.",
            "evidence": "Congress.gov title and summary verify the mismatch.",
            "evidence_basis": "official_title_and_provisions",
            "official_decision_source_url": (
                "https://www.congress.gov/bill/117th-congress/house-bill/1"
            ),
            "official_decision_text_version": "CRS Summary — Introduced",
            "official_decision_section": "Summary",
            "official_decision_evidence_excerpt": excerpt,
            "official_decision_evidence_sha256": audit.hashlib.sha256(
                excerpt.encode("utf-8")
            ).hexdigest(),
            "official_decision_retrieved_at": "2026-07-25",
            "official_decision_text_unavailable": False,
            "decision_provenance": ["preexisting decision"],
            "decision_type": "membership_category_relevance",
            "reason_code": "different_subject",
            "reviewer_confidence": "high",
            "adjudication_state": "reverified",
            "evidence_sources_checked": ["official_title", "official_crs_summary"],
            "reviewer": "test reviewer",
            "reviewed_at": "2026-07-25T00:00:00Z",
            "trusted_article_match_count": 0,
            "trusted_article_review_signal": (
                "no_trusted_article_match_not_dispositive"
            ),
            "official_text_evidence_state": None,
            "text_unavailable": True,
        }
    )
    return row


class PartitionAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.review_dir = Path(self.temp.name)
        self.rows = [
            base_row("116:hr:1", "exam", "include"),
            official_exclusion(),
            base_row("119:s:2", "capital", "unresolved"),
        ]
        self._write_review(self.rows)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _dump(self, path: Path, rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )

    def _write_review(self, rows: list[dict]) -> None:
        ledger = self.review_dir / "membership_review_ledger.ndjson"
        included = self.review_dir / "included_memberships.ndjson"
        excluded = self.review_dir / "false_positive_exclusions.ndjson"
        unresolved = self.review_dir / "unresolved_memberships.ndjson"
        sorted_queue = self.review_dir / "sorted_review_queue.ndjson"
        self._dump(ledger, rows)
        self._dump(
            included,
            [row for row in rows if row["disposition"] == "include"],
        )
        self._dump(
            excluded,
            [
                row
                for row in rows
                if row["disposition"] == "exclude_false_positive"
            ],
        )
        self._dump(
            unresolved,
            [row for row in rows if row["disposition"] == "unresolved"],
        )
        self._dump(sorted_queue, rows)
        output_rows = {
            "ledger": (ledger, len(rows)),
            "included": (
                included,
                sum(row["disposition"] == "include" for row in rows),
            ),
            "exclusions": (
                excluded,
                sum(
                    row["disposition"] == "exclude_false_positive"
                    for row in rows
                ),
            ),
            "unresolved": (
                unresolved,
                sum(row["disposition"] == "unresolved" for row in rows),
            ),
            "sorted_review_queue": (sorted_queue, len(rows)),
        }
        manifest = {
            "membership_count": len(rows),
            "distinct_review_membership_key_count": len(
                {(row["canonical_key"], row["query_id"]) for row in rows}
            ),
            "included_count": sum(
                row["disposition"] == "include" for row in rows
            ),
            "false_positive_exclusion_count": sum(
                row["disposition"] == "exclude_false_positive" for row in rows
            ),
            "unresolved_count": sum(
                row["disposition"] == "unresolved" for row in rows
            ),
            "article_absence_is_never_exclusion_evidence": True,
            "article_based_automated_exclusion_count": 0,
            "atomic_output_publish": True,
            "output_files_hash_bound": True,
            "outputs": {
                name: {
                    "path": str(path.resolve()),
                    "bytes": path.stat().st_size,
                    "sha256": audit.sha256_file(path),
                    "record_count": count,
                }
                for name, (path, count) in output_rows.items()
            },
        }
        (self.review_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def test_exact_partition_passes_and_classifies_enrichment_lag(self) -> None:
        report = audit.audit_review_partition(self.review_dir)
        self.assertEqual("PASS", report["status"])
        self.assertEqual(0, report["blocking_issue_count"])
        self.assertEqual(
            1,
            report["semantic_field_consistency"][
                "harmless_automated_enrichment_lag_count"
            ],
        )
        self.assertEqual(
            0,
            report["semantic_field_consistency"][
                "actual_provenance_inconsistency_count"
            ],
        )
        self.assertEqual(
            report["source_bindings"][
                "ledger_stable_membership_key_set_sha256"
            ],
            report["partition_exactness"][
                "partition_stable_membership_key_union_sha256"
            ],
        )
        payload_sha = report.pop("audit_payload_sha256")
        self.assertEqual(payload_sha, audit.canonical_json_sha256(report))

    def test_duplicate_membership_key_and_partition_loss_fail(self) -> None:
        duplicate = copy.deepcopy(self.rows[0])
        rows = self.rows + [duplicate]
        self._write_review(rows)
        # Leave a duplicate in the ledger but only one copy in the partition.
        self._dump(
            self.review_dir / "included_memberships.ndjson", [self.rows[0]]
        )
        report = audit.audit_review_partition(self.review_dir)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(
            ["116:hr:1::exam"],
            report["partition_exactness"]["duplicate_ledger_keys"],
        )

    def test_article_absence_cannot_be_negative_exclusion_evidence(self) -> None:
        rows = copy.deepcopy(self.rows)
        rows[1]["reason"] = "Exclude because no trusted article was found."
        self._write_review(rows)
        report = audit.audit_review_partition(self.review_dir)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(
            1,
            report["article_absence_policy"][
                "article_absence_used_negatively_count"
            ],
        )

    def test_incomplete_official_provenance_turns_mismatch_into_error(self) -> None:
        rows = copy.deepcopy(self.rows)
        rows[1]["official_decision_evidence_sha256"] = "0" * 64
        self._write_review(rows)
        report = audit.audit_review_partition(self.review_dir)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(
            1,
            report["semantic_field_consistency"][
                "actual_provenance_inconsistency_count"
            ],
        )
        self.assertTrue(
            report["preservation_and_provenance"][
                "exclusion_provenance_errors"
            ]
        )

    def test_manifest_output_hash_mismatch_fails_closed(self) -> None:
        manifest_path = self.review_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["outputs"]["ledger"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        report = audit.audit_review_partition(self.review_dir)
        self.assertEqual("FAIL", report["status"])
        self.assertIn(
            "outputs.ledger.sha256",
            " ".join(report["manifest_output_binding"]["errors"]),
        )


if __name__ == "__main__":
    unittest.main()
