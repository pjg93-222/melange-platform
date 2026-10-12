import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from staffer_network.littlesis_contract import profile_littlesis_pair
from staffer_network.publication import GenerationError, _validate_record_semantics
from staffer_network.schema_validation import SchemaValidationError, validate_instance
from staffer_network.source_manifest import canonical_json_bytes, sha256_file
from staffer_network.source_native_corpus import (
    SourceNativeCorpusError,
    build_source_native_corpus,
    relationship_precision,
)


def _entity(record_id, name):
    return {
        "type": "entities",
        "id": record_id,
        "attributes": {
            "id": record_id,
            "name": name,
            "aliases": [name + " alias", name + " ALIAS"],
            "primary_ext": "Person" if record_id == 1 else "Org",
            "types": ["Person" if record_id == 1 else "Organization"],
            "blurb": None,
            "parent_id": None,
            "start_date": None,
            "end_date": None,
            "updated_at": "2026-08-30T00:00:00Z",
        },
        "links": {"self": f"https://littlesis.example/entities/{record_id}"},
    }


def _relationship(record_id, endpoint_1, endpoint_2, category_id, **updates):
    attributes = {
        "id": record_id,
        "entity1_id": endpoint_1,
        "entity2_id": endpoint_2,
        "category_id": category_id,
        "description1": None,
        "description2": None,
        "description": None,
        "start_date": None,
        "end_date": None,
        "updated_at": "2026-08-30T00:00:00Z",
        "is_current": None,
        "filings": None,
        "category_attributes": None,
    }
    attributes.update(updates)
    return {
        "type": "relationships",
        "id": record_id,
        "attributes": attributes,
        "self": f"https://littlesis.example/relationships/{record_id}",
    }


def _write_gzip(path: Path, value):
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            stream.write(json.dumps(value, separators=(",", ":")).encode("utf-8"))


class StafferSourceNativeCorpusTests(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
        *,
        profile_lies_about_dangle=False,
        include_boss_controls=False,
    ):
        entities = root / "entities.json.gz"
        relationships = root / "relationships.json.gz"
        _write_gzip(entities, [_entity(1, "Person One"), _entity(2, "Org Two"), _entity(3, "School Three")])
        relationship_rows = [
            _relationship(10, 1, 2, 1),
            _relationship(11, 1, 3, 2, start_date="2013"),
            _relationship(12, 2, 3, 7, start_date="2010-00-00", filings=2),
            _relationship(13, 1, 2, 5),
            _relationship(14, 1, 999, 6),
        ]
        if include_boss_controls:
            relationship_rows.extend([
                _relationship(
                    15, 1, 2, 1, category_attributes={"boss_id": 3}
                ),
                _relationship(
                    16, 1, 2, 1, category_attributes={"boss_id": 999}
                ),
                _relationship(
                    17, 1, 2, 1, category_attributes={"boss_id": "not-an-id"}
                ),
                _relationship(
                    18, 1, 2, 1, category_attributes={"boss_id": 3.7}
                ),
                _relationship(
                    19, 1, 2, 1, category_attributes={"boss_id": True}
                ),
                _relationship(
                    20, 1, 2, 1, category_attributes={"boss_id": "3"}
                ),
            ])
        _write_gzip(relationships, relationship_rows)
        profile = profile_littlesis_pair(
            entities,
            relationships,
            generated_at="2026-08-30T06:30:00Z",
            collected_at="2026-08-30T06:24:00Z",
            relationship_prior_sha256=sha256_file(relationships),
        )
        if profile_lies_about_dangle:
            profile["relationship_dangling_rejected"] = 0
            profile["relationship_rejected"] = 0
            profile["relationship_accepted"] += 1
            profile["category_accepted"]["Transaction"] = 1
        profile_path = root / "profile.json"
        profile_path.write_bytes(canonical_json_bytes(profile))
        legacy = root / "legacy.ndjson"
        legacy_record = {
            "source_system_id": "quorum-legacy-derived",
            "source_entity_id": "1",
            "member_office_source_key": "Office One",
            "display_name": "Legacy Person",
            "title": "Chief of Staff",
            "featured": False,
        }
        legacy.write_bytes(canonical_json_bytes(legacy_record))
        legacy_pin = {
            "sha256": sha256_file(legacy), "bytes": legacy.stat().st_size, "records": 1,
        }
        legacy_receipt = root / "legacy_receipt.json"
        legacy_receipt.write_bytes(canonical_json_bytes({"record_file": {
            "records": 1, "sha256": legacy_pin["sha256"], "bytes": legacy_pin["bytes"],
        }}))
        pins = {
            "entities": {"sha256": sha256_file(entities), "bytes": entities.stat().st_size},
            "relationships": {"sha256": sha256_file(relationships), "bytes": relationships.stat().st_size},
        }
        return {
            "entities": entities,
            "relationships": relationships,
            "profile": profile_path,
            "profile_sha": sha256_file(profile_path),
            "legacy": legacy,
            "legacy_receipt": legacy_receipt,
            "legacy_pin": legacy_pin,
            "pins": pins,
        }

    def _build(self, fixture, output):
        return build_source_native_corpus(
            fixture["entities"], fixture["relationships"], fixture["profile"],
            fixture["legacy"], fixture["legacy_receipt"], output,
            generated_at="2026-08-30T07:00:00Z",
            synced_roots=[output.parent / "synced"],
            configured_corpus_root=output.parent,
            input_pins=fixture["pins"],
            expected_profile_sha256=fixture["profile_sha"],
            legacy_pin=fixture["legacy_pin"],
        )

    def test_date_precision_is_conservative_and_zero_padding_is_not_coerced(self):
        self.assertEqual(relationship_precision(None, None), "undated-only")
        self.assertEqual(relationship_precision("2013", None), "year")
        self.assertEqual(relationship_precision("2008-00-10", None), "year")
        self.assertEqual(relationship_precision("2013-05-00", "2014-06-15"), "month")
        self.assertEqual(relationship_precision("2013-05-02", None), "day")
        with self.assertRaisesRegex(SourceNativeCorpusError, "unsupported"):
            relationship_precision("Spring 2013", None)

    def test_fixture_build_conserves_scope_and_is_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = self._fixture(root)
            first = self._build(fixture, root / "generation-a")
            second = self._build(fixture, root / "generation-b")
            self.assertEqual(first, second)
            self.assertEqual(first["entities_emitted"], 3)
            self.assertEqual(first["relationship_emitted_in_scope"], 3)
            self.assertEqual(first["relationship_out_of_scope_category"], 1)
            self.assertEqual(first["dangling_relationship_ids"], [14])
            self.assertEqual(first["category_emitted"], {
                "Education": 1, "Lobbying": 1, "Position": 1,
            })
            self.assertEqual(first["category_out_of_scope"], {"Donation": 1})
            self.assertFalse(first["identity_resolution_advanced"])
            for name in (
                "littlesis_entities.ndjson.gz",
                "littlesis_relationship_assertions.ndjson.gz",
                "quorum_legacy_source_native_seed.ndjson",
                "source_native_corpus_receipt.json",
            ):
                self.assertEqual(
                    (root / "generation-a" / name).read_bytes(),
                    (root / "generation-b" / name).read_bytes(),
                )

            schemas = FEC / "staffer_network" / "schemas"
            entity_schema = json.loads((schemas / "littlesis_entity_assertion.schema.json").read_text(encoding="utf-8"))
            relation_schema = json.loads((schemas / "littlesis_relationship_assertion.schema.json").read_text(encoding="utf-8"))
            receipt_schema = json.loads((schemas / "source_native_corpus_receipt.schema.json").read_text(encoding="utf-8"))
            with gzip.open(root / "generation-a" / "littlesis_entities.ndjson.gz", "rt", encoding="utf-8") as stream:
                entity_rows = [json.loads(line) for line in stream]
            with gzip.open(root / "generation-a" / "littlesis_relationship_assertions.ndjson.gz", "rt", encoding="utf-8") as stream:
                relation_rows = [json.loads(line) for line in stream]
            for row in entity_rows:
                validate_instance(row, entity_schema)
                _validate_record_semantics(row, row["schema_id"])
            self.assertEqual(
                entity_rows[0]["aliases"], ["Person One ALIAS", "Person One alias"]
            )
            for row in relation_rows:
                validate_instance(row, relation_schema)
                _validate_record_semantics(row, row["schema_id"])
            validate_instance(first, receipt_schema)
            _validate_record_semantics(first, first["schema_id"])

    def test_boss_controls_preserve_valid_and_redact_dangling_or_malformed_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = self._fixture(root, include_boss_controls=True)
            receipt = self._build(fixture, root / "generation")
            relationship_path = (
                root / "generation" / "littlesis_relationship_assertions.ndjson.gz"
            )
            with gzip.open(relationship_path, "rt", encoding="utf-8") as stream:
                rows = {
                    row["source_numeric_id"]: row
                    for row in map(json.loads, stream)
                }

            self.assertEqual(receipt["boss_references_redacted"], 4)
            self.assertEqual(
                rows[15]["source_category_attributes"]["boss_source_entity_id"],
                "littlesis:entity:3",
            )
            self.assertEqual(
                rows[20]["source_category_attributes"]["boss_source_entity_id"],
                "littlesis:entity:3",
            )
            self.assertIsNone(
                rows[16]["source_category_attributes"]["boss_source_entity_id"]
            )
            self.assertIsNone(
                rows[17]["source_category_attributes"]["boss_source_entity_id"]
            )
            self.assertIsNone(
                rows[18]["source_category_attributes"]["boss_source_entity_id"]
            )
            self.assertIsNone(
                rows[19]["source_category_attributes"]["boss_source_entity_id"]
            )
            with gzip.open(relationship_path, "rb") as stream:
                self.assertNotIn(b"littlesis:entity:999", stream.read())

    def test_registered_legacy_row_schema_rejects_shape_and_namespace_drift(self):
        schema = json.loads((
            FEC / "staffer_network" / "schemas" /
            "legacy_source_native_record.schema.json"
        ).read_text(encoding="utf-8"))
        row = {
            "source_system_id": "quorum-legacy-derived",
            "source_entity_id": "123",
            "member_office_source_key": "Office One",
            "display_name": "Legacy Person",
            "title": "Chief of Staff",
            "featured": False,
        }
        validate_instance(row, schema)
        with self.assertRaises(SchemaValidationError):
            validate_instance(dict(row, email="private@example.test"), schema)
        with self.assertRaises(SchemaValidationError):
            validate_instance(dict(row, source_system_id="crossed-source"), schema)
        with self.assertRaises(SchemaValidationError):
            validate_instance(dict(row, featured="false"), schema)

    def test_wrong_input_hash_and_false_profile_refuse_to_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = self._fixture(root)
            bad = copy.deepcopy(fixture)
            bad["pins"] = copy.deepcopy(fixture["pins"])
            bad["pins"]["entities"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(SourceNativeCorpusError, "sealed byte pin"):
                self._build(bad, root / "bad-hash")

            lying_root = root / "lying"
            lying_root.mkdir()
            lying = self._fixture(lying_root, profile_lies_about_dangle=True)
            with self.assertRaisesRegex(SourceNativeCorpusError, "sealed profile"):
                self._build(lying, root / "bad-dangle")

    def test_scope_namespace_and_quarantine_negative_controls(self):
        schemas = FEC / "staffer_network" / "schemas"
        relation_schema = json.loads((schemas / "littlesis_relationship_assertion.schema.json").read_text(encoding="utf-8"))
        corpus_schema = json.loads((schemas / "source_native_corpus_receipt.schema.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = self._fixture(root)
            receipt = self._build(fixture, root / "generation")
            with gzip.open(root / "generation" / "littlesis_relationship_assertions.ndjson.gz", "rt", encoding="utf-8") as stream:
                row = json.loads(next(stream))

            donation = dict(row, category_id=5, category_name="Donation")
            with self.assertRaises(SchemaValidationError):
                validate_instance(donation, relation_schema)
            crossed = dict(row, subject_source_entity_id="quorum:entity:q1")
            with self.assertRaises(SchemaValidationError):
                validate_instance(crossed, relation_schema)
            crossed_boss = copy.deepcopy(row)
            crossed_boss["source_category_attributes"][
                "boss_source_entity_id"
            ] = "quorum:entity:1"
            with self.assertRaisesRegex(GenerationError, "boss reference crosses"):
                _validate_record_semantics(
                    crossed_boss, crossed_boss["schema_id"]
                )
            enabled = dict(receipt, source_enabled=True)
            with self.assertRaises(SchemaValidationError):
                validate_instance(enabled, corpus_schema)
            malformed = copy.deepcopy(receipt)
            malformed["relationship_out_of_scope_category"] += 1
            with self.assertRaisesRegex(GenerationError, "conservation"):
                _validate_record_semantics(malformed, malformed["schema_id"])

    def test_restricted_generation_refuses_synced_or_existing_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = self._fixture(root)
            synced = root / "synced"
            with self.assertRaisesRegex(SourceNativeCorpusError, "outside synced roots"):
                build_source_native_corpus(
                    fixture["entities"], fixture["relationships"], fixture["profile"],
                    fixture["legacy"], fixture["legacy_receipt"], synced / "generation",
                    generated_at="2026-08-30T07:00:00Z", synced_roots=[synced],
                    configured_corpus_root=synced,
                    input_pins=fixture["pins"], expected_profile_sha256=fixture["profile_sha"],
                    legacy_pin=fixture["legacy_pin"],
                )
            outside = root / "outside"
            with self.assertRaisesRegex(SourceNativeCorpusError, "configured machine-local root"):
                build_source_native_corpus(
                    fixture["entities"], fixture["relationships"], fixture["profile"],
                    fixture["legacy"], fixture["legacy_receipt"], outside,
                    generated_at="2026-08-30T07:00:00Z", synced_roots=[],
                    configured_corpus_root=root / "configured",
                    input_pins=fixture["pins"], expected_profile_sha256=fixture["profile_sha"],
                    legacy_pin=fixture["legacy_pin"],
                )
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(SourceNativeCorpusError, "already exists"):
                self._build(fixture, existing)

    def test_committed_corpus_and_determinism_receipts_are_binding(self):
        receipt_directory = FEC.parent / "data" / "staffer_network" / "receipts"
        schema_directory = FEC / "staffer_network" / "schemas"
        corpus_path = receipt_directory / "source_native_corpus_20260830.json"
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        corpus_schema = json.loads((
            schema_directory / "source_native_corpus_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(corpus, corpus_schema)
        _validate_record_semantics(corpus, corpus["schema_id"])
        self.assertEqual(corpus["entities_emitted"], 443140)
        self.assertEqual(corpus["relationship_emitted_in_scope"], 424323)
        self.assertEqual(corpus["relationship_out_of_scope_category"], 1456165)
        self.assertEqual(corpus["undated_position_emitted"], 226370)
        self.assertEqual(corpus["dangling_relationship_ids"], [1548275, 1629551, 1652624])
        self.assertEqual(corpus["category_vocabulary"]["5"], "Donation")
        self.assertEqual(corpus["category_vocabulary"]["7"], "Lobbying")
        self.assertEqual(corpus["boss_references_redacted"], 0)

        determinism = json.loads((
            receipt_directory / "source_native_corpus_determinism_20260830.json"
        ).read_text(encoding="utf-8"))
        determinism_schema = json.loads((
            schema_directory / "source_native_corpus_determinism_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(determinism, determinism_schema)
        _validate_record_semantics(determinism, determinism["schema_id"])
        self.assertEqual(
            determinism["accepted_corpus_receipt_sha256"], sha256_file(corpus_path)
        )

        validation = json.loads((
            receipt_directory / "source_native_corpus_validation_20260830.json"
        ).read_text(encoding="utf-8"))
        validation_schema = json.loads((
            schema_directory / "source_native_corpus_validation_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(validation, validation_schema)
        _validate_record_semantics(validation, validation["schema_id"])
        self.assertEqual(validation["corpus_receipt_sha256"], sha256_file(corpus_path))
        self.assertEqual(validation["total_records_validated"], 873236)


if __name__ == "__main__":
    unittest.main()
