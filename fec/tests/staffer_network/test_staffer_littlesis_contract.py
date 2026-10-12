import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from staffer_network.littlesis_contract import profile_littlesis_pair
from staffer_network.publication import _validate_record_semantics
from staffer_network.schema_validation import validate_instance
from staffer_network.source_manifest import sha256_file


def _record(record_id, attributes):
    return {"type": "fixture", "id": record_id, "attributes": {
        "id": record_id, **attributes,
    }}


def _write_gzip(path: Path, value):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(value, stream, separators=(",", ":"))


class StafferLittleSisContractTests(unittest.TestCase):
    def _profile(self, root: Path, relationships):
        entities_path = root / "entities.json.gz"
        relationships_path = root / "relationships.json.gz"
        _write_gzip(entities_path, [
            _record(1, {"name": "One"}),
            _record(2, {"name": "Two"}),
        ])
        _write_gzip(relationships_path, relationships)
        receipt = profile_littlesis_pair(
            entities_path,
            relationships_path,
            generated_at="2026-08-30T06:30:00Z",
            collected_at="2026-08-30T06:24:00Z",
            relationship_prior_sha256=sha256_file(relationships_path),
        )
        schema = json.loads((
            FEC / "staffer_network" / "schemas" /
            "littlesis_profile_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(receipt, schema)
        _validate_record_semantics(receipt, receipt["schema_id"])
        return receipt

    def test_clean_pair_passes_with_zero_accepted_dangles(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = self._profile(Path(temp), [
                _record(10, {
                    "entity1_id": 1, "entity2_id": 2, "category_id": 1,
                    "start_date": None, "end_date": None,
                }),
                _record(11, {
                    "entity1_id": 2, "entity2_id": 1, "category_id": 2,
                    "start_date": "2020-00-00", "end_date": None,
                }),
            ])
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["relationship_accepted"], 2)
            self.assertEqual(receipt["accepted_dangling_relationships"], 0)
            self.assertEqual(receipt["undated_position_accepted"], 1)
            self.assertFalse(receipt["source_enabled"])

    def test_invalid_duplicate_and_dangling_records_are_disjoint_rejections(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = self._profile(Path(temp), [
                _record(10, {
                    "entity1_id": 1, "entity2_id": 2, "category_id": 1,
                    "start_date": None, "end_date": None,
                }),
                _record(11, {
                    "entity1_id": 1, "entity2_id": 3, "category_id": 2,
                }),
                _record(10, {
                    "entity1_id": 1, "entity2_id": 2, "category_id": 7,
                }),
                _record(12, {"entity1_id": 1, "category_id": 7}),
            ])
            self.assertEqual(receipt["status"], "QUARANTINED")
            self.assertEqual(receipt["relationship_observed"], 4)
            self.assertEqual(receipt["relationship_accepted"], 1)
            self.assertEqual(receipt["relationship_rejected"], 3)
            self.assertEqual(receipt["relationship_dangling_rejected"], 1)
            self.assertEqual(receipt["relationship_duplicates"], 1)
            self.assertEqual(receipt["relationship_invalid"], 1)
            self.assertFalse(receipt["source_native_ready"])

    def test_committed_profile_receipt_is_public_safe_and_semantically_valid(self):
        receipt = json.loads((
            FEC.parent / "data" / "staffer_network" / "receipts" /
            "littlesis_profile_20260830.json"
        ).read_text(encoding="utf-8"))
        schema = json.loads((
            FEC / "staffer_network" / "schemas" /
            "littlesis_profile_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(receipt, schema)
        _validate_record_semantics(receipt, receipt["schema_id"])
        self.assertEqual(receipt["entity_observed"], 443140)
        self.assertEqual(receipt["relationship_observed"], 1880491)
        self.assertEqual(receipt["relationship_dangling_rejected"], 3)
        self.assertEqual(receipt["relationship_accepted"], 1880488)
        self.assertEqual(receipt["category_observed"]["Position"], 368995)
        self.assertEqual(receipt["category_observed"]["Education"], 44673)
        self.assertEqual(receipt["category_observed"]["Lobbying"], 10655)
        self.assertEqual(receipt["category_observed"]["Donation"], 1267716)
        self.assertFalse(receipt["source_enabled"])


if __name__ == "__main__":
    unittest.main()
