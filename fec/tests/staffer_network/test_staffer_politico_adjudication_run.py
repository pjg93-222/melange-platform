import json
from pathlib import Path
import sys
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(FEC))
sys.path.insert(0, str(TEST_ROOT))

from staffer_network.politico_adjudication import (
    AdjudicationFrameworkError,
    REVIEWERS,
    build_adjudication_framework,
)
from staffer_network.politico_adjudication_run import (
    BATCH_SCHEMA_ID,
    append_label_batch,
    build_post_label_receipt,
    prepare_reviewer_views,
)
from staffer_network.publication import _validate_record_semantics
from staffer_network.schema_validation import SchemaValidationError, validate_instance
from staffer_network.source_manifest import canonical_json_bytes, sha256_bytes, sha256_file
from staffer_network.validate import assert_public_safe
from test_staffer_politico_adjudication import _fixture, _packet_rows, _receipt_anchor


class StafferPoliticoAdjudicationRunTests(unittest.TestCase):
    def _framework(self, root: Path) -> tuple[Path, str]:
        pilot, manifest = _fixture(root)
        framework = root / "restricted" / "framework"
        build_adjudication_framework(
            pilot, manifest, framework,
            allocation_seed="fixture-seed-v1",
            generated_at="2026-08-30T05:30:00Z",
            synced_roots=[root / "synced"],
        )
        return framework, _receipt_anchor(framework)

    def test_views_batches_and_aggregate_receipt_preserve_quarantine(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            framework, anchor = self._framework(root)
            views = root / "restricted" / "views"
            view_receipt = prepare_reviewer_views(
                framework, views,
                expected_receipt_sha256=anchor,
                synced_roots=[root / "synced"],
                generated_at="2026-08-30T05:31:00Z",
            )
            self.assertEqual(view_receipt["status"], "PASS")
            self.assertEqual(len(view_receipt["views"]), 4)
            self.assertFalse(view_receipt["cross_reviewer_visibility"])
            self.assertFalse(view_receipt["machine_control_visibility"])

            split = json.loads((
                framework / "sealed_controls" / "split_seal.json"
            ).read_text(encoding="utf-8"))
            calibration_ids = sorted(
                candidate_id for candidate_id, partition in
                split["partition_by_candidate"].items()
                if partition == "CALIBRATION"
            )
            for reviewer_id in REVIEWERS:
                for partition in ("CALIBRATION", "HOLDOUT"):
                    leaf = views / reviewer_id / partition.casefold()
                    self.assertEqual(
                        {path.name for path in leaf.iterdir()},
                        {"packets.ndjson", "instructions.md", "label_batch_output.schema.json"},
                    )
                    rows = _packet_rows(leaf / "packets.ndjson")
                    labels = []
                    for row in rows:
                        candidate_id = row["candidate_id"]
                        if partition == "HOLDOUT":
                            outcome = "MATCH"
                            reasons = ["NAME_COMPATIBLE", "TITLE_COMPATIBLE"]
                        elif candidate_id == calibration_ids[0]:
                            outcome = "MATCH"
                            reasons = ["NAME_COMPATIBLE", "OFFICE_COMPATIBLE"]
                        elif reviewer_id == REVIEWERS[0]:
                            outcome = "DISTINCT"
                            reasons = ["IDENTIFIER_CONFLICT"]
                        else:
                            outcome = "POSSIBLE"
                            reasons = ["TEMPORAL_EVIDENCE_MISSING"]
                        labels.append({
                            "candidate_id": candidate_id,
                            "outcome": outcome,
                            "reason_codes": reasons,
                            "rationale": "Synthetic bounded-evidence fixture judgment.",
                        })
                    response = {
                        "schema_id": BATCH_SCHEMA_ID,
                        "reviewer_id": reviewer_id,
                        "partition": partition,
                        "labels": labels,
                    }
                    result = append_label_batch(
                        framework,
                        expected_receipt_sha256=anchor,
                        response=response,
                        submitted_at="2026-08-30T05:32:00Z",
                    )
                    self.assertEqual(result["labels_added"], 2)
                    if partition == "HOLDOUT":
                        self.assertNotIn("outcome_counts", result)
                    else:
                        self.assertIn("outcome_counts", result)

            results_root = root / "restricted" / "results"
            receipt = build_post_label_receipt(
                framework, results_root,
                expected_receipt_sha256=anchor,
                generated_at="2026-08-30T05:33:00Z",
                synced_roots=[root / "synced"],
            )
            schema = json.loads((
                FEC / "staffer_network" / "schemas" /
                "politico_adjudication_results_receipt.schema.json"
            ).read_text(encoding="utf-8"))
            validate_instance(receipt, schema)
            _validate_record_semantics(receipt, receipt["schema_id"])
            self.assertEqual(receipt["submitted_label_count"], 8)
            self.assertEqual(receipt["reviewed_candidate_count"], 4)
            self.assertEqual(receipt["calibration"]["exact_agreement_count"], 1)
            self.assertEqual(receipt["calibration"]["determinate_pair_count"], 1)
            self.assertEqual(receipt["calibration"]["consensus_match_count"], 1)
            self.assertEqual(receipt["calibration"]["disagreement_queue_count"], 1)
            self.assertEqual(receipt["holdout"]["status"], "QUARANTINED")
            self.assertFalse(receipt["holdout"]["outcome_counts_published"])
            self.assertNotIn("reviewer_outcome_counts", receipt["holdout"])
            self.assertIsNone(receipt["estimated_human_gold_precision"])
            self.assertTrue(receipt["model_assisted_only"])

    def test_batch_requires_exact_assignment_before_any_append(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            framework, anchor = self._framework(root)
            views = root / "restricted" / "views"
            prepare_reviewer_views(
                framework, views,
                expected_receipt_sha256=anchor,
                synced_roots=[root / "synced"],
                generated_at="2026-08-30T05:31:00Z",
            )
            rows = _packet_rows(
                views / REVIEWERS[0] / "calibration" / "packets.ndjson"
            )
            response = {
                "schema_id": BATCH_SCHEMA_ID,
                "reviewer_id": REVIEWERS[0],
                "partition": "CALIBRATION",
                "labels": [{
                    "candidate_id": rows[0]["candidate_id"],
                    "outcome": "POSSIBLE",
                    "reason_codes": ["INSUFFICIENT_EVIDENCE"],
                    "rationale": "Only one of two assignments was returned.",
                }],
            }
            with self.assertRaises(SchemaValidationError):
                append_label_batch(
                    framework,
                    expected_receipt_sha256=anchor,
                    response=response,
                    submitted_at="2026-08-30T05:32:00Z",
                )
            self.assertFalse((framework / "labels" / f"{REVIEWERS[0]}.ndjson").exists())

    def test_results_fail_closed_on_split_or_label_partition_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            framework, anchor = self._framework(root)
            views = root / "restricted" / "views"
            prepare_reviewer_views(
                framework, views,
                expected_receipt_sha256=anchor,
                synced_roots=[root / "synced"],
                generated_at="2026-08-30T05:31:00Z",
            )
            for reviewer_id in REVIEWERS:
                for partition in ("CALIBRATION", "HOLDOUT"):
                    rows = _packet_rows(
                        views / reviewer_id / partition.casefold() / "packets.ndjson"
                    )
                    response = {
                        "schema_id": BATCH_SCHEMA_ID,
                        "reviewer_id": reviewer_id,
                        "partition": partition,
                        "labels": [{
                            "candidate_id": row["candidate_id"],
                            "outcome": "MATCH",
                            "reason_codes": ["NAME_COMPATIBLE", "TITLE_COMPATIBLE"],
                            "rationale": "Synthetic bounded-evidence fixture judgment.",
                        } for row in rows],
                    }
                    append_label_batch(
                        framework,
                        expected_receipt_sha256=anchor,
                        response=response,
                        submitted_at="2026-08-30T05:32:00Z",
                    )

            split_path = framework / "sealed_controls" / "split_seal.json"
            original_split = split_path.read_bytes()
            split = json.loads(original_split)
            candidate_id = sorted(split["partition_by_candidate"])[0]
            split["partition_by_candidate"][candidate_id] = (
                "HOLDOUT" if split["partition_by_candidate"][candidate_id] == "CALIBRATION"
                else "CALIBRATION"
            )
            split_path.write_bytes(canonical_json_bytes(split))
            with self.assertRaisesRegex(AdjudicationFrameworkError, "split seal hash mismatch"):
                build_post_label_receipt(
                    framework, root / "restricted" / "tampered-split-results",
                    expected_receipt_sha256=anchor,
                    generated_at="2026-08-30T05:33:00Z",
                    synced_roots=[root / "synced"],
                )
            split_path.write_bytes(original_split)

            label_path = framework / "labels" / f"{REVIEWERS[0]}.ndjson"
            labels = _packet_rows(label_path)
            target = labels[0]
            target["partition"] = (
                "HOLDOUT" if target["partition"] == "CALIBRATION" else "CALIBRATION"
            )
            payload = dict(target)
            payload.pop("label_id")
            target["label_id"] = "label:" + sha256_bytes(canonical_json_bytes(payload))
            label_path.write_bytes(b"".join(canonical_json_bytes(row) for row in labels))
            with self.assertRaisesRegex(
                AdjudicationFrameworkError,
                "label partition does not match the sealed split",
            ):
                build_post_label_receipt(
                    framework, root / "restricted" / "tampered-label-results",
                    expected_receipt_sha256=anchor,
                    generated_at="2026-08-30T05:33:00Z",
                    synced_roots=[root / "synced"],
                )

    def test_committed_results_receipt_is_public_safe_and_semantically_valid(self):
        receipt_root = FEC.parent / "data" / "staffer_network" / "receipts"
        framework_path = receipt_root / "politico_adjudication_framework_20260830.json"
        results_path = receipt_root / "politico_adjudication_results_20260830.json"
        self.assertEqual(
            sha256_file(framework_path),
            "729c653278dedfb65a410f89764206430ee78df7e7a517b94a04cc300757e845",
        )
        self.assertEqual(
            sha256_file(results_path),
            "9d8b8ac83362d02b8c551ac4aa9445ed8766e3f440a1d2f759f0c25939f79117",
        )
        receipt = json.loads(results_path.read_text(encoding="utf-8"))
        schema = json.loads((
            FEC / "staffer_network" / "schemas" /
            "politico_adjudication_results_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(receipt, schema)
        _validate_record_semantics(receipt, receipt["schema_id"])
        assert_public_safe(receipt)
        self.assertEqual(receipt["submitted_label_count"], 400)
        self.assertEqual(receipt["reviewed_candidate_count"], 200)
        self.assertEqual(receipt["holdout"]["status"], "QUARANTINED")
        self.assertFalse(receipt["holdout"]["outcome_counts_published"])
        self.assertNotIn("reviewer_outcome_counts", receipt["holdout"])
        self.assertEqual(receipt["human_gold_adjudicated_count"], 0)
        self.assertIsNone(receipt["estimated_human_gold_precision"])
        self.assertFalse(receipt["source_enabled"])
        self.assertEqual(receipt["canonical_entity_merges"], 0)


if __name__ == "__main__":
    unittest.main()
