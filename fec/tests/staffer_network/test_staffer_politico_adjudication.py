import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from staffer_network.politico_adjudication import (
    AdjudicationFrameworkError,
    EXCLUDED_MACHINE_FIELDS,
    REVIEWERS,
    append_label,
    build_adjudication_framework,
    request_canonical_merge,
    verify_adjudication_framework,
)
from staffer_network.publication import _validate_record_semantics
from staffer_network.schema_validation import SchemaValidationError, validate_instance
from staffer_network.source_manifest import canonical_json_bytes, seal_source_manifest
from staffer_network.validate import assert_public_safe


def _candidate(index: int) -> dict:
    digest = hashlib.sha256(f"candidate-{index}".encode()).hexdigest()
    return {
        "candidate_id": "candidate:" + digest,
        "disposition": "STRICT_CANDIDATE_NAME_TITLE_OFFICE",
        "source_profile_uuid": f"politico-{index}",
        "politico_name": f"Alex Example {index}",
        "politico_roles": [f"Chief of Staff, Office of Member {index}"],
        "legacy_staff_id": f"legacy-{index}",
        "legacy_name": f"Alex Example {index}",
        "legacy_title": "Chief of Staff",
        "legacy_member_name": f"Member {index}",
        "title_match": True,
        "office_evidence_available": True,
        "office_match": True,
        "review_status": "PENDING",
    }


def _manifest() -> dict:
    return seal_source_manifest({
        "schema_id": "staffer-network-source-manifest-v1",
        "source_system_id": "politico-pro-staffer-directory",
        "enabled": False,
        "authority_scope": "synthetic authorized adjudication fixture",
        "license": "synthetic fixture",
        "terms_uri": "urn:test:politico-adjudication",
        "terms_checked_at": "2026-08-30T04:00:00Z",
        "authorization_status": "AUTHORIZED",
        "approved_access_mode": "sealed local fixture",
        "query_or_inventory": "four synthetic strict candidates",
        "crawl_watermark": "fixture",
        "retention_policy": "delete after test",
        "source_schema_version": "fixture-v1",
        "freshness_rule": "immutable fixture",
        "redistribution_class": "restricted-local-only",
        "source_as_of": "2026-08-30T04:00:00Z",
        "collected_at": "2026-08-30T04:00:01Z",
        "collector_version": "fixture-v1",
        "parser_version": "fixture-v1",
        "expected_count": 4,
        "observed_count": 4,
        "accepted_count": 4,
        "rejected_count": 0,
        "duplicate_count": 0,
        "amendment_policy": "replace by version",
        "rejection_reasons": {},
        "acceptance_bar": {
            "metric": "accepted / expected", "threshold": 1.0,
            "comparison": ">=", "observed": 1.0, "pass": True,
        },
        "files": [{
            "path": "fixture.ndjson", "sha256": "0" * 64, "bytes": 1,
        }],
    })


def _fixture(root: Path) -> tuple[Path, Path]:
    pilot = root / "pilot"
    pilot.mkdir()
    sample = [_candidate(index) for index in range(4)]
    sample_body = b"".join(canonical_json_bytes(row) for row in sample)
    (pilot / "strict_candidate_review_sample.ndjson").write_bytes(sample_body)
    receipt = {
        "schema_id": "politico-directory-resolution-pilot-receipt-v1",
        "generated_at": "2026-08-30T04:01:00Z",
        "status": "REPORT",
        "method_version": "fixture-v1",
        "review_sample_count": 4,
        "review_sample_sha256": hashlib.sha256(sample_body).hexdigest(),
        "adjudicated_sample_count": 0,
        "estimated_precision": None,
        "canonical_entity_merges": 0,
        "auto_merge_enabled": False,
        "adjudication_required": True,
        "legacy_outcomes": {"REVIEW_NAME_COLLISION": 6},
        "dispositions": {"REVIEW_NAME_COLLISION": 9},
        "failures": [],
    }
    (pilot / "resolution_pilot_receipt.json").write_bytes(
        canonical_json_bytes(receipt)
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(_manifest()))
    return pilot, manifest_path


def _packet_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _receipt_anchor(output: Path) -> str:
    return hashlib.sha256((output / "framework_receipt.json").read_bytes()).hexdigest()


class StafferPoliticoAdjudicationTests(unittest.TestCase):
    def test_committed_framework_receipt_is_public_safe_and_valid(self):
        receipt = json.loads((
            FEC.parent / "data" / "staffer_network" / "receipts" /
            "politico_adjudication_framework_20260830.json"
        ).read_text(encoding="utf-8"))
        schema = json.loads((
            FEC / "staffer_network" / "schemas" /
            "politico_adjudication_framework_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(receipt, schema)
        _validate_record_semantics(receipt, receipt["schema_id"])
        assert_public_safe(receipt)
        self.assertEqual(receipt["review_sample_count"], 200)
        self.assertEqual(receipt["review_assignments"], 400)
        self.assertEqual(receipt["submitted_label_count"], 0)
        self.assertEqual(receipt["canonical_entity_merges"], 0)

    def test_framework_blinds_splits_and_conserves(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pilot, manifest = _fixture(root)
            output = root / "restricted" / "a1"
            receipt = build_adjudication_framework(
                pilot, manifest, output,
                allocation_seed="fixture-seed-v1",
                generated_at="2026-08-30T04:02:00Z",
                synced_roots=[root / "synced"],
            )
            schema = json.loads((
                FEC / "staffer_network" / "schemas" /
                "politico_adjudication_framework_receipt.schema.json"
            ).read_text(encoding="utf-8"))
            validate_instance(receipt, schema)
            _validate_record_semantics(receipt, receipt["schema_id"])
            assert_public_safe(receipt)

            self.assertEqual(receipt["calibration_count"], 2)
            self.assertEqual(receipt["holdout_count"], 2)
            self.assertEqual(receipt["review_assignments"], 8)
            self.assertEqual(receipt["submitted_label_count"], 0)
            self.assertEqual(receipt["adjudicated_candidate_count"], 0)
            self.assertIsNone(receipt["estimated_precision"])
            self.assertEqual(receipt["legacy_row_collision_count"], 6)
            self.assertEqual(receipt["politico_profile_collision_count"], 9)
            self.assertFalse(receipt["source_enabled"])
            self.assertFalse(receipt["source_canonical_use_enabled"])
            self.assertEqual(receipt["canonical_entity_merges"], 0)
            self.assertFalse(receipt["auto_merge_enabled"])
            verification = verify_adjudication_framework(
                output, expected_receipt_sha256=_receipt_anchor(output)
            )
            self.assertEqual(verification["status"], "PASS")
            self.assertEqual(verification["verified_review_records"], 8)

            seen = {reviewer: set() for reviewer in REVIEWERS}
            for packet_file in (output / "reviewer_packets").glob("*.ndjson"):
                rows = _packet_rows(packet_file)
                for row in rows:
                    self.assertFalse(EXCLUDED_MACHINE_FIELDS & set(row))
                    self.assertEqual(set(row["record_a"]), {
                        "record_id", "display_name", "role_context", "office_context",
                    })
                    self.assertEqual(set(row["record_b"]), {
                        "record_id", "display_name", "role_context", "office_context",
                    })
                    seen[row["reviewer_id"]].add(row["candidate_id"])
            self.assertEqual(seen[REVIEWERS[0]], seen[REVIEWERS[1]])
            self.assertEqual(len(seen[REVIEWERS[0]]), 4)

            split = json.loads((
                output / "sealed_controls" / "split_seal.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(split["sealed_before_label_count"], 0)
            self.assertEqual(len(split["partition_by_candidate"]), 4)

    def test_framework_is_byte_deterministic_for_same_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pilot, manifest = _fixture(root)
            receipts = []
            digests = []
            for name in ("a", "b"):
                output = root / "restricted" / name
                receipts.append(build_adjudication_framework(
                    pilot, manifest, output,
                    allocation_seed="fixture-seed-v1",
                    generated_at="2026-08-30T04:02:00Z",
                    synced_roots=[root / "synced"],
                ))
                digests.append({
                    str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in output.rglob("*") if path.is_file()
                })
            self.assertNotEqual(receipts[0]["root_binding_sha256"], receipts[1]["root_binding_sha256"])
            excluded = {"framework_receipt.json", "sealed_controls\\root_binding.json"}
            core_a = {path: digest for path, digest in digests[0].items() if path not in excluded}
            core_b = {path: digest for path, digest in digests[1].items() if path not in excluded}
            self.assertEqual(core_a, core_b)
            for name in ("a", "b"):
                output = root / "restricted" / name
                self.assertEqual(
                    verify_adjudication_framework(
                        output, expected_receipt_sha256=_receipt_anchor(output)
                    )["status"],
                    "PASS",
                )

    def test_label_store_is_independent_append_only_and_no_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pilot, manifest = _fixture(root)
            output = root / "restricted" / "a1"
            receipt = build_adjudication_framework(
                pilot, manifest, output,
                allocation_seed="fixture-seed-v1",
                generated_at="2026-08-30T04:02:00Z",
                synced_roots=[root / "synced"],
            )
            allocation = json.loads((
                output / "sealed_controls" / "allocation_manifest.json"
            ).read_text(encoding="utf-8"))
            assignment = allocation["assignments"][0]
            label = append_label(
                output,
                expected_receipt_sha256=_receipt_anchor(output),
                candidate_id=assignment["candidate_id"],
                reviewer_id=assignment["reviewer_id"],
                partition=assignment["partition"],
                outcome="MATCH",
                reason_codes=["NAME_COMPATIBLE", "TITLE_COMPATIBLE"],
                rationale="The bounded pair evidence is mutually consistent.",
                submitted_at="2026-08-30T04:03:00Z",
            )
            self.assertEqual(label["outcome"], "MATCH")
            with self.assertRaisesRegex(AdjudicationFrameworkError, "already labeled"):
                append_label(
                    output,
                    expected_receipt_sha256=_receipt_anchor(output),
                    candidate_id=assignment["candidate_id"],
                    reviewer_id=assignment["reviewer_id"],
                    partition=assignment["partition"],
                    outcome="MATCH",
                    reason_codes=["NAME_COMPATIBLE"],
                    rationale="Duplicate attempt.",
                    submitted_at="2026-08-30T04:04:00Z",
                )
            other = allocation["assignments"][1]
            with self.assertRaisesRegex(AdjudicationFrameworkError, "prior review"):
                append_label(
                    output,
                    expected_receipt_sha256=_receipt_anchor(output),
                    candidate_id=other["candidate_id"],
                    reviewer_id=other["reviewer_id"],
                    partition=other["partition"],
                    outcome="ABSTAIN",
                    reason_codes=["INSUFFICIENT_EVIDENCE"],
                    rationale="Prior label was visible.",
                    submitted_at="2026-08-30T04:04:00Z",
                    prior_review_visible=True,
                )
            with self.assertRaisesRegex(SchemaValidationError, "longer than 2000"):
                append_label(
                    output,
                    expected_receipt_sha256=_receipt_anchor(output),
                    candidate_id=other["candidate_id"],
                    reviewer_id=other["reviewer_id"],
                    partition=other["partition"],
                    outcome="ABSTAIN",
                    reason_codes=["INSUFFICIENT_EVIDENCE"],
                    rationale="x" * 3000,
                    submitted_at="2026-08-30T04:04:00Z",
                )
            with self.assertRaisesRegex(AdjudicationFrameworkError, "canonical merge prohibited"):
                request_canonical_merge(receipt)

    def test_external_anchor_allocation_hash_and_root_binding_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pilot, manifest = _fixture(root)

            anchored = root / "restricted" / "anchored"
            build_adjudication_framework(
                pilot, manifest, anchored,
                allocation_seed="fixture-seed-v1",
                generated_at="2026-08-30T04:02:00Z",
                synced_roots=[root / "synced"],
            )
            anchor_hash = _receipt_anchor(anchored)
            allocation = json.loads((
                anchored / "sealed_controls" / "allocation_manifest.json"
            ).read_text(encoding="utf-8"))
            assignment = allocation["assignments"][0]
            receipt_path = anchored / "framework_receipt.json"
            mutated_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            mutated_receipt["generated_at"] = "2026-08-30T04:02:01Z"
            receipt_path.write_bytes(canonical_json_bytes(mutated_receipt))
            with self.assertRaisesRegex(AdjudicationFrameworkError, "external anchor"):
                verify_adjudication_framework(
                    anchored, expected_receipt_sha256=anchor_hash
                )
            with self.assertRaisesRegex(AdjudicationFrameworkError, "external anchor"):
                append_label(
                    anchored,
                    expected_receipt_sha256=anchor_hash,
                    candidate_id=assignment["candidate_id"],
                    reviewer_id=assignment["reviewer_id"],
                    partition=assignment["partition"],
                    outcome="ABSTAIN",
                    reason_codes=["INSUFFICIENT_EVIDENCE"],
                    rationale="Receipt was tampered.",
                    submitted_at="2026-08-30T04:04:00Z",
                )

            forged = root / "restricted" / "forged"
            build_adjudication_framework(
                pilot, manifest, forged,
                allocation_seed="fixture-seed-v1",
                generated_at="2026-08-30T04:02:00Z",
                synced_roots=[root / "synced"],
            )
            forged_anchor = _receipt_anchor(forged)
            allocation_path = forged / "sealed_controls" / "allocation_manifest.json"
            forged_allocation = json.loads(allocation_path.read_text(encoding="utf-8"))
            forged_allocation["assignments"].append({
                "candidate_id": "candidate:" + "f" * 64,
                "reviewer_id": REVIEWERS[0],
                "partition": "CALIBRATION",
            })
            allocation_path.write_bytes(canonical_json_bytes(forged_allocation))
            with self.assertRaisesRegex(AdjudicationFrameworkError, "allocation manifest hash"):
                append_label(
                    forged,
                    expected_receipt_sha256=forged_anchor,
                    candidate_id="candidate:" + "f" * 64,
                    reviewer_id=REVIEWERS[0],
                    partition="CALIBRATION",
                    outcome="ABSTAIN",
                    reason_codes=["INSUFFICIENT_EVIDENCE"],
                    rationale="Forged assignment.",
                    submitted_at="2026-08-30T04:04:00Z",
                )

            original = root / "restricted" / "original"
            build_adjudication_framework(
                pilot, manifest, original,
                allocation_seed="fixture-seed-v1",
                generated_at="2026-08-30T04:02:00Z",
                synced_roots=[root / "synced"],
            )
            original_anchor = _receipt_anchor(original)
            copied = root / "synced" / "copied"
            copied.parent.mkdir()
            shutil.copytree(original, copied)
            copied_allocation = json.loads((
                copied / "sealed_controls" / "allocation_manifest.json"
            ).read_text(encoding="utf-8"))
            copied_assignment = copied_allocation["assignments"][0]
            with self.assertRaisesRegex(AdjudicationFrameworkError, "moved"):
                verify_adjudication_framework(
                    copied, expected_receipt_sha256=original_anchor
                )
            with self.assertRaisesRegex(AdjudicationFrameworkError, "moved"):
                append_label(
                    copied,
                    expected_receipt_sha256=original_anchor,
                    candidate_id=copied_assignment["candidate_id"],
                    reviewer_id=copied_assignment["reviewer_id"],
                    partition=copied_assignment["partition"],
                    outcome="ABSTAIN",
                    reason_codes=["INSUFFICIENT_EVIDENCE"],
                    rationale="Copied root.",
                    submitted_at="2026-08-30T04:04:00Z",
                )

    def test_fail_closed_on_synced_output_tampering_and_source_enablement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pilot, manifest = _fixture(root)
            synced = root / "synced"
            with self.assertRaisesRegex(AdjudicationFrameworkError, "synced root"):
                build_adjudication_framework(
                    pilot, manifest, synced / "a1",
                    allocation_seed="fixture-seed-v1",
                    generated_at="2026-08-30T04:02:00Z",
                    synced_roots=[synced],
                )

            sample = pilot / "strict_candidate_review_sample.ndjson"
            sample.write_bytes(sample.read_bytes() + canonical_json_bytes(_candidate(9)))
            with self.assertRaisesRegex(AdjudicationFrameworkError, "hash differs"):
                build_adjudication_framework(
                    pilot, manifest, root / "restricted" / "tampered",
                    allocation_seed="fixture-seed-v1",
                    generated_at="2026-08-30T04:02:00Z",
                    synced_roots=[synced],
                )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pilot, manifest = _fixture(root)
            enabled = _manifest()
            enabled.pop("manifest_sha256")
            enabled["enabled"] = True
            manifest.write_bytes(canonical_json_bytes(seal_source_manifest(enabled)))
            receipt = build_adjudication_framework(
                pilot, manifest, root / "restricted" / "enabled",
                allocation_seed="fixture-seed-v1",
                generated_at="2026-08-30T04:02:00Z",
                synced_roots=[root / "synced"],
            )
            self.assertTrue(receipt["source_enabled"])


if __name__ == "__main__":
    unittest.main()
