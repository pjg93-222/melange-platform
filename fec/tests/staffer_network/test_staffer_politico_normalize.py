import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from staffer_network.politico_normalize import (
    PoliticoNormalizationError,
    normalize_politico_snapshot,
    resolution_pilot,
)
from staffer_network.publication import GenerationError, _validate_record_semantics
from staffer_network.schema_validation import validate_instance
from staffer_network.source_manifest import seal_source_manifest, validate_manifest_files


def _fixture(root: Path, rows: list[dict]) -> dict:
    bodies = {
        "directory_rows.ndjson": "".join(
            json.dumps(row, separators=(",", ":")) + "\n" for row in rows
        ).encode("utf-8"),
        "request_log.ndjson": b'{"status":"OK"}\n',
        "partition_inventory.json": b'{"total":1}\n',
    }
    for name, body in bodies.items():
        (root / name).write_bytes(body)
    files = [
        {
            "path": name,
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
        }
        for name, body in bodies.items()
    ]
    return seal_source_manifest({
        "schema_id": "staffer-network-source-manifest-v1",
        "source_system_id": "politico-pro-staffer-directory",
        "enabled": True,
        "authority_scope": "synthetic authorized fixture",
        "license": "synthetic fixture",
        "terms_uri": "urn:test:politico-permission",
        "terms_checked_at": "2026-08-30T03:00:00Z",
        "authorization_status": "AUTHORIZED",
        "approved_access_mode": "sealed local fixture",
        "query_or_inventory": f"{len(rows)} synthetic rows",
        "crawl_watermark": "fixture",
        "retention_policy": "delete after test",
        "source_schema_version": "fixture-v1",
        "freshness_rule": "immutable fixture",
        "redistribution_class": "public-safe-derived-only",
        "source_as_of": "2026-08-30T03:00:00Z",
        "collected_at": "2026-08-30T03:00:01Z",
        "collector_version": "fixture-v1",
        "parser_version": "fixture-v1",
        "expected_count": len(rows),
        "observed_count": len(rows),
        "accepted_count": len(rows),
        "rejected_count": 0,
        "duplicate_count": 0,
        "amendment_policy": "replace by version",
        "rejection_reasons": {},
        "acceptance_bar": {
            "metric": "accepted / expected", "threshold": 1.0,
            "comparison": ">=", "observed": 1.0, "pass": True,
        },
        "files": files,
    })


class StafferPoliticoNormalizeTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{
            "partition": "Democratic",
            "partition_ordinal": 1,
            "profile_uuid": "profile-1",
            "name": "Alex Example",
            "roles": ["Chief of Staff, Office of John Member"],
            "issue_areas": ["Budget"],
        }]

    def test_normalization_conserves_and_resolution_never_merges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "raw"
            raw.mkdir()
            manifest = _fixture(raw, self.rows)
            normalized = root / "normalized"
            receipt = normalize_politico_snapshot(
                raw, manifest, normalized, generated_at="2026-08-30T03:01:00Z"
            )
            schema = json.loads((
                FEC / "staffer_network" / "schemas" /
                "politico_normalization_receipt.schema.json"
            ).read_text(encoding="utf-8"))
            validate_instance(receipt, schema)
            _validate_record_semantics(receipt, receipt["schema_id"])
            self.assertEqual(receipt["source_profiles"], 1)
            self.assertEqual(receipt["normalized_persons"], 1)
            self.assertEqual(receipt["role_assertions"], 1)
            self.assertEqual(receipt["issue_assertions"], 1)

            legacy = root / "legacy.json"
            legacy.write_text(json.dumps({
                "John Member": [{
                    "id": "legacy-1", "n": "Alex Example",
                    "t": "Chief of Staff", "star": 0,
                }]
            }), encoding="utf-8")
            pilot = resolution_pilot(
                normalized, legacy, root / "pilot",
                generated_at="2026-08-30T03:02:00Z", sample_size=200,
            )
            pilot_schema = json.loads((
                FEC / "staffer_network" / "schemas" /
                "politico_resolution_pilot_receipt.schema.json"
            ).read_text(encoding="utf-8"))
            validate_instance(pilot, pilot_schema)
            _validate_record_semantics(pilot, pilot["schema_id"])
            self.assertEqual(pilot["strict_candidate_count"], 1)
            self.assertEqual(pilot["review_sample_count"], 1)
            self.assertEqual(sum(pilot["legacy_outcomes"].values()), 1)
            self.assertEqual(pilot["canonical_entity_merges"], 0)
            self.assertFalse(pilot["auto_merge_enabled"])

    def test_source_shape_contact_and_immutability_are_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "raw"
            raw.mkdir()
            manifest = _fixture(raw, self.rows)
            output = root / "normalized"
            normalize_politico_snapshot(
                raw, manifest, output, generated_at="2026-08-30T03:01:00Z"
            )
            with self.assertRaisesRegex(PoliticoNormalizationError, "already exists"):
                normalize_politico_snapshot(
                    raw, manifest, output, generated_at="2026-08-30T03:01:00Z"
                )

        for label, mutation in (
            ("extra field", lambda row: row.update({"employer": "Example"})),
            ("contact value", lambda row: row.update({"roles": ["Call 202-555-0123"]})),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                raw = root / "raw"
                raw.mkdir()
                rows = json.loads(json.dumps(self.rows))
                mutation(rows[0])
                manifest = _fixture(raw, rows)
                with self.assertRaises((ValueError, PoliticoNormalizationError)):
                    normalize_politico_snapshot(
                        raw, manifest, root / "normalized",
                        generated_at="2026-08-30T03:01:00Z",
                    )

    def test_receipt_semantics_have_planted_failures(self):
        normalized = {
            "schema_id": "politico-directory-normalization-receipt-v1",
            "status": "PASS", "source_profiles": 1, "normalized_persons": 1,
            "role_assertions": 1, "issue_assertions": 1,
            "contact_findings": 0, "canonical_entity_merges": 0, "failures": [],
            "person_fields": [
                "display_name", "name_normalized", "observed_at", "partition_ordinal",
                "party_partition", "source_person_id", "source_profile_uuid",
            ],
            "role_fields": [
                "assertion_id", "observed_at", "predicate", "role_ordinal", "role_text",
                "source_person_id", "source_profile_uuid",
            ],
            "issue_fields": [
                "assertion_id", "issue_area", "issue_ordinal", "observed_at", "predicate",
                "source_person_id", "source_profile_uuid",
            ],
            "files": [
                {"path": "persons.ndjson", "records": 1},
                {"path": "role_assertions.ndjson", "records": 1},
                {"path": "issue_assertions.ndjson", "records": 1},
            ],
        }
        for label, mutate in (
            ("person loss", lambda value: value.update({"normalized_persons": 0})),
            ("contact", lambda value: value.update({"contact_findings": 1})),
            ("merge", lambda value: value.update({"canonical_entity_merges": 1})),
        ):
            with self.subTest(label=label):
                value = json.loads(json.dumps(normalized))
                mutate(value)
                with self.assertRaises(GenerationError):
                    _validate_record_semantics(value, value["schema_id"])

        pilot = {
            "schema_id": "politico-directory-resolution-pilot-receipt-v1",
            "status": "REPORT", "candidate_count": 1,
            "dispositions": {
                "STRICT_CANDIDATE_NAME_TITLE_OFFICE": 1,
                "REVIEW_NAME_TITLE_NO_OFFICE_EVIDENCE": 0,
                "REVIEW_NAME_TITLE_OFFICE_CONFLICT": 0,
                "REVIEW_NAME_ONLY": 0, "REVIEW_NAME_COLLISION": 0,
            },
            "strict_candidate_count": 1, "review_sample_count": 1,
            "exact_name_overlap_profiles": 1, "politico_persons": 1,
            "exact_name_overlap_legacy_rows": 1, "legacy_staff_rows": 1,
            "legacy_outcomes": {
                "PENDING_UNIQUE_NAME_CANDIDATE": 1,
                "REVIEW_NAME_COLLISION": 0, "NO_EXACT_NAME_OVERLAP": 0,
            },
            "review_sample_requested": 200, "adjudicated_sample_count": 0,
            "estimated_precision": None, "canonical_entity_merges": 0,
            "auto_merge_enabled": False, "adjudication_required": True,
            "failures": [],
        }
        for field, value in (
            ("canonical_entity_merges", 1),
            ("auto_merge_enabled", True),
            ("adjudication_required", False),
            ("estimated_precision", 1.0),
        ):
            with self.subTest(field=field):
                mutated = json.loads(json.dumps(pilot))
                mutated[field] = value
                with self.assertRaises(GenerationError):
                    _validate_record_semantics(mutated, mutated["schema_id"])

    def test_committed_receipts_and_evidence_manifest_are_sealed(self):
        receipt_root = FEC.parent / "data" / "staffer_network" / "receipts"
        for filename, schema_filename in (
            ("politico_normalization_20260829.json", "politico_normalization_receipt.schema.json"),
            ("politico_resolution_pilot_20260829.json", "politico_resolution_pilot_receipt.schema.json"),
        ):
            with self.subTest(filename=filename):
                receipt = json.loads((receipt_root / filename).read_text(encoding="utf-8"))
                schema = json.loads((
                    FEC / "staffer_network" / "schemas" / schema_filename
                ).read_text(encoding="utf-8"))
                validate_instance(receipt, schema)
                _validate_record_semantics(receipt, receipt["schema_id"])
        manifest = json.loads((
            FEC.parent / "data" / "staffer_network" / "policy" /
            "politico_normalization_evidence_manifest_20260829.json"
        ).read_text(encoding="utf-8"))
        validate_manifest_files(manifest, FEC.parent)


if __name__ == "__main__":
    unittest.main()
