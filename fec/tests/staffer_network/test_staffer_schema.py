import json
from pathlib import Path
import sqlite3
import sys
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from staffer_network.publication import SCHEMA_FILES, schema_path
from staffer_network.schema_validation import (
    SchemaValidationError, validate_instance, validate_schema_definition,
)


class StafferSchemaTests(unittest.TestCase):
    def test_sql_schema_installs_required_ledger_tables(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript((FEC / "staffer_network" / "schema.sql").read_text(encoding="utf-8"))
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {
            "source_system", "ingest_run", "raw_record", "entity", "source_entity",
            "source_entity_observation", "resolution_decision", "entity_membership",
            "identifier_assertion", "relation_assertion", "consolidation_build",
            "canonical_fact", "fact_support", "network_build", "network_edge",
            "network_edge_support", "lda_activity_lobbyist_observation",
        }
        self.assertTrue(required.issubset(tables))

    def test_source_native_membership_does_not_require_a_fake_pair_decision(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript((FEC / "staffer_network" / "schema.sql").read_text(encoding="utf-8"))
        connection.execute("INSERT INTO source_system VALUES (?,?,?,?,?,?,?)", ("s", "Synthetic", "fixture", "https://example.test", "CC0", "public", "AUTHORIZED"))
        connection.execute("INSERT INTO ingest_run VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("r", "s", "2026-08-29T00:00:00Z", None, "{}", "v1", "v1", 1, 1, 1, 0, "complete", "a" * 64))
        connection.execute("INSERT INTO raw_record VALUES (?,?,?,?,?,?,?,?,?,?)", ("raw", "r", "person", "p", None, "b" * 64, "application/json", None, None, "{}"))
        connection.execute("INSERT INTO source_entity VALUES (?,?,?,?,?)", ("se", "s", "person", "p", "raw"))
        connection.execute("INSERT INTO entity VALUES (?,?,?,?)", ("e", "person", "2026-08-29T00:00:00Z", None))
        connection.execute("INSERT INTO entity_membership VALUES (?,?,?,?,?,?)", ("se", "e", None, "SOURCE_NATIVE", "2026-08-29T00:00:00Z", None))
        self.assertEqual(connection.execute("SELECT assignment_method FROM entity_membership").fetchone()[0], "SOURCE_NATIVE")

    def test_all_registered_json_schemas_exist_and_parse(self):
        directory = FEC / "staffer_network" / "schemas"
        self.assertEqual(len(SCHEMA_FILES), 55)
        for schema_id in SCHEMA_FILES:
            document = json.loads(schema_path(directory, schema_id).read_text(encoding="utf-8"))
            self.assertEqual(document["$schema"], "https://json-schema.org/draft/2020-12/schema")
            validate_schema_definition(document)

    def test_schema_maximum_keywords_fail_closed(self):
        cases = (
            ("abcd", {"type": "string", "maxLength": 3}, "longer than 3"),
            ([1, 2], {"type": "array", "maxItems": 1}, "more than 1"),
            (2, {"type": "integer", "maximum": 1}, "above maximum 1"),
        )
        for value, schema, message in cases:
            with self.subTest(schema=schema), self.assertRaisesRegex(
                SchemaValidationError, message
            ):
                validate_instance(value, schema)

    def test_object_cardinality_typed_extras_and_unknown_keywords_fail_closed(self):
        schema = {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": {"type": "integer", "minimum": 0},
        }
        with self.assertRaisesRegex(SchemaValidationError, "fewer than 1 properties"):
            validate_instance({}, schema)
        with self.assertRaisesRegex(SchemaValidationError, "expected type"):
            validate_instance({"bad": "one"}, schema)
        with self.assertRaisesRegex(SchemaValidationError, "below minimum"):
            validate_instance({"bad": -1}, schema)
        validate_instance({"good": 1}, schema)
        with self.assertRaisesRegex(SchemaValidationError, "unsupported schema keywords"):
            validate_instance(1, {"type": "integer", "silentlyIgnored": True})


if __name__ == "__main__":
    unittest.main()
