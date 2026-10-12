import json
import json
from pathlib import Path
import sys
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from staffer_network.publication import (
    GenerationError, canonical_ndjson_gzip, deterministic_gzip,
    load_current_generation, publish_generation, RESTRICTED_SCHEMA_IDS,
    SCHEMA_FILES,
)
from staffer_network.source_manifest import canonical_json_bytes, seal_source_manifest, sha256_bytes
from staffer_network.validate import PublicSafetyError


def edge(**changes):
    value = {
        "edge_id": "edge:1", "layer": "staffer-person", "predicate": "WORKED_WITH",
        "subject_person_id": "person:1", "object_person_id": "person:2",
        "directed": False, "raw_weight": 1.0, "scaled_weight": None,
        "evidence_class": "synthetic", "precision_bucket": "day",
        "support_count": 1, "support_digest": "b" * 64,
    }
    value.update(changes)
    return value


def metric(**changes):
    value = {
        "metric_id": "I-11", "metric_definition_version": "v1",
        "lifecycle_state": "INVARIANT", "manifest_sha256": ["a" * 64],
        "provenance_manifest_state": "SEALED",
        "as_of": "2026-08-29T20:00:00Z", "measured_at": "2026-08-29T20:00:00Z",
        "eligible_denominator_count": 1, "cohort_query": "synthetic fixture",
        "numerator_count": 1, "statistic": 1.0, "confidence_interval": None,
        "threshold": 1.0, "comparison": ">=", "strata": [], "exclusions": [],
        "evidence_artifact_uri": "fixture://i-11", "evidence_sha256": "c" * 64,
        "result": "PASS", "not_evaluable_reason": None,
        "consequence_class": "ABORT_BUILD", "owner": "staffer-network",
    }
    value.update(changes)
    return value


def coverage(generation_id="g1", metrics=None):
    return {
        "schema_id": "staffer-network-coverage-v1",
        "generation_id": generation_id,
        "metrics": metrics or [metric()],
    }


def fixture_registry(root):
    source = Path(root) / "input.json"
    source.write_bytes(b"{}")
    document = seal_source_manifest({
        "schema_id": "staffer-network-source-manifest-v1", "source_system_id": "synthetic",
        "enabled": True, "authority_scope": "publication fixture", "license": "CC0",
        "terms_uri": "https://example.test/terms", "terms_checked_at": "2026-08-29T20:00:00Z",
        "authorization_status": "AUTHORIZED", "approved_access_mode": "local fixture",
        "query_or_inventory": "one synthetic file", "crawl_watermark": "fixture",
        "retention_policy": "temporary fixture", "source_schema_version": "fixture-v1",
        "freshness_rule": "immutable fixture", "redistribution_class": "public",
        "source_as_of": "2026-08-29T20:00:00Z", "collected_at": "2026-08-29T20:00:00Z",
        "collector_version": "fixture-v1", "parser_version": "fixture-v1",
        "expected_count": 1, "observed_count": 1, "accepted_count": 1, "rejected_count": 0,
        "duplicate_count": 0, "amendment_policy": "replace by version", "rejection_reasons": {},
        "acceptance_bar": {"metric": "accepted / expected", "threshold": 1.0, "comparison": ">=", "observed": 1.0, "pass": True},
        "files": [{"path": "input.json", "sha256": sha256_bytes(b"{}"), "bytes": 2}],
    })
    return {"fixture://source-manifest": (document, root)}


class StafferPublicationTests(unittest.TestCase):
    def test_deterministic_gzip_and_immutable_generation(self):
        self.assertEqual(deterministic_gzip(b"payload"), deterministic_gzip(b"payload"))
        body, count = canonical_ndjson_gzip([edge(edge_id="edge:2"), edge()])
        with tempfile.TemporaryDirectory() as temp:
            artifacts = {
                "person_edges.ndjson.gz": (body, "staffer-network-person-edge-v1", count),
                "coverage.json": (canonical_json_bytes(coverage()), "staffer-network-coverage-v1", 1),
            }
            registry = fixture_registry(temp)
            publish_generation(temp, generation_id="g1", build_as_of="2026-08-29T20:00:00Z", derivation_version="fixture-v1", source_manifest_registry=registry, artifacts=artifacts)
            manifest, loaded = load_current_generation(temp)
            self.assertEqual(manifest["generation_id"], "g1")
            self.assertIn("person_edges.ndjson.gz", loaded)
            with self.assertRaisesRegex(GenerationError, "immutable generation"):
                publish_generation(temp, generation_id="g1", build_as_of="2026-08-29T20:00:00Z", derivation_version="fixture-v1", source_manifest_registry=registry, artifacts=artifacts)

    def test_schema_and_privacy_fail_closed(self):
        with self.assertRaises(PublicSafetyError):
            canonical_ndjson_gzip([edge(email="private@example.test")])
        with self.assertRaises(PublicSafetyError):
            canonical_ndjson_gzip([edge(evidence_class="call 202-555-0188")])
        with self.assertRaises(PublicSafetyError):
            canonical_ndjson_gzip([edge(evidence_class="private@example.test")])
        invalid_body, count = canonical_ndjson_gzip([edge(unexpected="no")])
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(GenerationError, "schema validation failed"):
                publish_generation(temp, generation_id="bad", build_as_of="2026-08-29T20:00:00Z", derivation_version="fixture-v1", source_manifest_registry=fixture_registry(temp), artifacts={"person_edges.ndjson.gz": (invalid_body, "staffer-network-person-edge-v1", count), "coverage.json": (canonical_json_bytes(coverage("bad")), "staffer-network-coverage-v1", 1)})

    def test_restricted_legacy_schema_cannot_enter_public_generation(self):
        legacy = canonical_json_bytes({
            "source_system_id": "quorum-legacy-derived",
            "source_entity_id": "123",
            "member_office_source_key": "Office One",
            "display_name": "Legacy Person",
            "title": "Chief of Staff",
            "featured": False,
        })
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(
                GenerationError, "restricted-local-only schema cannot be published"
            ):
                publish_generation(
                    temp,
                    generation_id="restricted",
                    build_as_of="2026-08-30T00:00:00Z",
                    derivation_version="fixture-v1",
                    source_manifest_registry=fixture_registry(temp),
                    artifacts={
                        "coverage.json": (
                            canonical_json_bytes(coverage("restricted")),
                            "staffer-network-coverage-v1",
                            1,
                        ),
                        "legacy.ndjson": (
                            legacy,
                            "staffer-network-legacy-source-native-record-v1",
                            1,
                        ),
                    },
                )

    def test_review_and_legacy_schemas_remain_nonpublic_but_public_sources_do_not(self):
        body, count = canonical_ndjson_gzip([{}])
        self.assertTrue(RESTRICTED_SCHEMA_IDS.issubset(SCHEMA_FILES))
        self.assertTrue({
            "staffer-network-littlesis-entity-assertion-v1",
            "staffer-network-littlesis-relationship-assertion-v1",
            "staffer-network-lda-industry-edge-v1",
        }.isdisjoint(RESTRICTED_SCHEMA_IDS))
        self.assertTrue({
            "staffer-network-lda-identity-candidate-v1",
            "staffer-network-legacy-source-native-record-v1",
        }.issubset(RESTRICTED_SCHEMA_IDS))
        for schema_id in sorted(RESTRICTED_SCHEMA_IDS):
            with self.subTest(schema_id=schema_id), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(
                    GenerationError,
                    "restricted-local-only schema cannot be published",
                ):
                    publish_generation(
                        temp,
                        generation_id="restricted-lda-candidate",
                        build_as_of="2026-08-30T00:00:00Z",
                        derivation_version="fixture-v1",
                        source_manifest_registry=fixture_registry(temp),
                        artifacts={
                            "coverage.json": (
                                canonical_json_bytes(coverage("restricted-lda-candidate")),
                                "staffer-network-coverage-v1",
                                1,
                            ),
                            "candidate.ndjson.gz": (body, schema_id, count),
                        },
                    )

    def test_forged_restricted_schema_cannot_be_loaded(self):
        body, count = canonical_ndjson_gzip([edge()])
        for schema_id in sorted(RESTRICTED_SCHEMA_IDS):
            schema_file = SCHEMA_FILES[schema_id]
            with self.subTest(schema_id=schema_id), tempfile.TemporaryDirectory() as temp:
                publish_generation(
                    temp,
                    generation_id="g1",
                    build_as_of="2026-08-30T00:00:00Z",
                    derivation_version="fixture-v1",
                    source_manifest_registry=fixture_registry(temp),
                    artifacts={
                        "person_edges.ndjson.gz": (
                            body, "staffer-network-person-edge-v1", count
                        ),
                        "coverage.json": (
                            canonical_json_bytes(coverage()),
                            "staffer-network-coverage-v1",
                            1,
                        ),
                    },
                )
                root = Path(temp)
                manifest_path = (
                    root / "generations" / "g1" / "staffer_network_manifest.json"
                )
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                entry = next(
                    row for row in manifest["files"]
                    if row["path"] == "person_edges.ndjson.gz"
                )
                entry["schema_id"] = schema_id
                entry["schema_sha256"] = sha256_bytes((
                    FEC / "staffer_network" / "schemas" / schema_file
                ).read_bytes())
                manifest_body = canonical_json_bytes(manifest)
                manifest_path.write_bytes(manifest_body)
                pointer_path = root / "staffer_network_current.json"
                pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                pointer["manifest_sha256"] = sha256_bytes(manifest_body)
                pointer_path.write_bytes(canonical_json_bytes(pointer))
                with self.assertRaisesRegex(
                    GenerationError, "restricted-local-only schema cannot be loaded"
                ):
                    load_current_generation(temp)

    def test_lda_edge_cannot_be_relabelled_as_generic_for_publication(self):
        lda_edge = {
            "schema_id": "staffer-network-lda-industry-edge-v1",
            "edge_id": "lda:edge:" + "a" * 64,
            "predicate": "NAMED_ON_ACTIVITY",
            "subject_id": "lda:lobbyist:1",
            "subject_kind": "person",
            "object_id": "lda:activity:fixture:0",
            "object_kind": "lda_activity",
            "raw_weight": 1.0,
            "precision_bucket": "day",
            "support_count": 1,
            "support_digest": "b" * 64,
        }
        body, count = canonical_ndjson_gzip([lda_edge])
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(
            GenerationError, "schema validation failed"
        ):
            publish_generation(
                temp,
                generation_id="relabelled-lda-edge",
                build_as_of="2026-08-30T00:00:00Z",
                derivation_version="fixture-v1",
                source_manifest_registry=fixture_registry(temp),
                artifacts={
                    "coverage.json": (
                        canonical_json_bytes(coverage("relabelled-lda-edge")),
                        "staffer-network-coverage-v1",
                        1,
                    ),
                    "industry_edges.ndjson.gz": (
                        body, "staffer-network-industry-edge-v1", count
                    ),
                },
            )

    def test_mixed_generation_or_tamper_is_rejected(self):
        body, count = canonical_ndjson_gzip([edge()])
        with tempfile.TemporaryDirectory() as temp:
            publish_generation(temp, generation_id="g1", build_as_of="2026-08-29T20:00:00Z", derivation_version="fixture-v1", source_manifest_registry=fixture_registry(temp), artifacts={"person_edges.ndjson.gz": (body, "staffer-network-person-edge-v1", count), "coverage.json": (canonical_json_bytes(coverage()), "staffer-network-coverage-v1", 1)})
            target = Path(temp) / "generations" / "g1" / "person_edges.ndjson.gz"
            target.write_bytes(target.read_bytes() + b"tamper")
            with self.assertRaisesRegex(GenerationError, "(?:byte count|content hash) mismatch"):
                load_current_generation(temp)

    def test_semantic_graph_and_metric_invariants_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            self_loop, count = canonical_ndjson_gzip([edge(object_person_id="person:1")])
            with self.assertRaisesRegex(GenerationError, "self-loop"):
                publish_generation(temp, generation_id="loop", build_as_of="2026-08-29T20:00:00Z", derivation_version="fixture-v1", source_manifest_registry=fixture_registry(temp), artifacts={"person_edges.ndjson.gz": (self_loop, "staffer-network-person-edge-v1", count), "coverage.json": (canonical_json_bytes(coverage("loop")), "staffer-network-coverage-v1", 1)})
        with tempfile.TemporaryDirectory() as temp:
            vacuous = coverage("vac", [metric(eligible_denominator_count=0, numerator_count=0)])
            with self.assertRaisesRegex(GenerationError, "zero denominator"):
                publish_generation(temp, generation_id="vac", build_as_of="2026-08-29T20:00:00Z", derivation_version="fixture-v1", source_manifest_registry=fixture_registry(temp), artifacts={"coverage.json": (canonical_json_bytes(vacuous), "staffer-network-coverage-v1", 1)})

    def test_nonfinite_and_typed_endpoint_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "Out of range float"):
                canonical_ndjson_gzip([edge(raw_weight=float("nan"))])

            member = {"edge_id": "member:1", "member_a_bioguide": "A000001", "member_b_bioguide": "A000001", "predicate": "SHARED_PERSONNEL_CONCURRENT", "raw_weight": 1.0, "contributing_person_count": 1, "support_digest": "d" * 64}
            member_body, member_count = canonical_ndjson_gzip([member])
            with self.assertRaisesRegex(GenerationError, "member overlay endpoints"):
                publish_generation(temp, generation_id="member", build_as_of="2026-08-29T20:00:00Z", derivation_version="fixture-v1", source_manifest_registry=fixture_registry(temp), artifacts={"member_overlay.ndjson.gz": (member_body, "staffer-network-member-overlay-v1", member_count), "coverage.json": (canonical_json_bytes(coverage("member")), "staffer-network-coverage-v1", 1)})

            industry = {"edge_id": "industry:1", "predicate": "FILED_BY", "subject_id": "person:1", "subject_kind": "person", "object_id": "client:1", "object_kind": "client", "raw_weight": 1.0, "precision_bucket": "undated-only", "support_count": 1, "support_digest": "e" * 64}
            industry_body, industry_count = canonical_ndjson_gzip([industry])
            with self.assertRaisesRegex(GenerationError, "invalid endpoint kinds"):
                publish_generation(temp, generation_id="industry", build_as_of="2026-08-29T20:00:00Z", derivation_version="fixture-v1", source_manifest_registry=fixture_registry(temp), artifacts={"industry_edges.ndjson.gz": (industry_body, "staffer-network-industry-edge-v1", industry_count), "coverage.json": (canonical_json_bytes(coverage("industry")), "staffer-network-coverage-v1", 1)})

    def test_disabled_source_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = fixture_registry(temp)
            document, base = registry["fixture://source-manifest"]
            disabled = dict(document)
            disabled.update({
                "enabled": False,
                "authorization_status": "PROHIBITED",
                "redistribution_class": "prohibited",
                "acceptance_bar": {
                    "metric": "accepted / expected", "threshold": 1.0,
                    "comparison": ">=", "observed": 0.0, "pass": False,
                },
            })
            disabled = seal_source_manifest(disabled)
            with self.assertRaisesRegex(GenerationError, "not enabled and authorized"):
                publish_generation(
                    temp, generation_id="disabled", build_as_of="2026-08-29T20:00:00Z",
                    derivation_version="fixture-v1",
                    source_manifest_registry={"fixture://source-manifest": (disabled, base)},
                    artifacts={
                        "coverage.json": (
                            canonical_json_bytes(coverage("disabled")),
                            "staffer-network-coverage-v1", 1,
                        )
                    },
                )

    def test_restricted_redistribution_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = fixture_registry(temp)
            document, base = registry["fixture://source-manifest"]
            restricted = dict(document)
            restricted["redistribution_class"] = "restricted-local-only"
            restricted = seal_source_manifest(restricted)
            with self.assertRaisesRegex(
                GenerationError, "redistribution class is not public"
            ):
                publish_generation(
                    temp,
                    generation_id="restricted-redistribution",
                    build_as_of="2026-08-30T00:00:00Z",
                    derivation_version="fixture-v1",
                    source_manifest_registry={
                        "fixture://source-manifest": (restricted, base)
                    },
                    artifacts={
                        "coverage.json": (
                            canonical_json_bytes(
                                coverage("restricted-redistribution")
                            ),
                            "staffer-network-coverage-v1",
                            1,
                        )
                    },
                )

    def test_generation_inventory_rejects_unadvertised_file(self):
        body, count = canonical_ndjson_gzip([edge()])
        with tempfile.TemporaryDirectory() as temp:
            publish_generation(temp, generation_id="g1", build_as_of="2026-08-29T20:00:00Z", derivation_version="fixture-v1", source_manifest_registry=fixture_registry(temp), artifacts={"person_edges.ndjson.gz": (body, "staffer-network-person-edge-v1", count), "coverage.json": (canonical_json_bytes(coverage()), "staffer-network-coverage-v1", 1)})
            (Path(temp) / "generations" / "g1" / "unadvertised.txt").write_text("private", encoding="utf-8")
            with self.assertRaisesRegex(GenerationError, "inventory is not closed"):
                load_current_generation(temp)


if __name__ == "__main__":
    unittest.main()
