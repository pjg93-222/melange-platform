# -*- coding: utf-8 -*-

from __future__ import annotations

import gzip
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy import sparse
import yaml

from fec.staffer_network.canonical_facts import build_canonical_generation
from fec.staffer_network.graph_build import build_network_generation
from fec.staffer_network.network_math import (
    balanced_symmetric_adjacency,
    bridging_diversity,
    combine_independent_support,
    componentwise_eigenvector_centrality,
    connectedness_pagerank,
    deduplicate_undirected_layer_edges,
    group_balanced_teleport,
    layer_balanced_transition,
)
from fec.staffer_network.schema_validation import validate_instance
from fec.staffer_network.source_manifest import validate_manifest_files


def _write_rows(path: Path, rows: list[dict]) -> None:
    if path.suffix == ".gz":
        with path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
                for row in rows:
                    stream.write((json.dumps(row, sort_keys=True) + "\n").encode())
    else:
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class NetworkMathTests(unittest.TestCase):
    def test_layer_balancing_and_connectedness(self):
        transition = layer_balanced_transition(
            4,
            [0, 0, 0, 1],
            [1, 2, 3, 2],
            [1.0, 1.0, 1.0, 1.0],
            [0, 0, 1, 0],
        )
        self.assertAlmostEqual(float(transition[0, 1] + transition[0, 2]), 0.5)
        self.assertAlmostEqual(float(transition[0, 3]), 0.5)
        teleport = group_balanced_teleport([0, 0, 1, 1])
        rank, receipt = connectedness_pagerank(
            transition, damping=0.85, teleport=teleport
        )
        self.assertAlmostEqual(float(rank.sum()), 1.0)
        self.assertLess(receipt["residual_l1"], 1e-12)
        diversity = bridging_diversity(transition, [0, 0, 1, 1])
        self.assertGreater(diversity[0], 0.0)

    def test_componentwise_eigenvector_does_not_localize_across_components(self):
        adjacency = sparse.csr_matrix(
            np.array(
                [
                    [0, 2, 0, 0, 0],
                    [2, 0, 0, 0, 0],
                    [0, 0, 0, 1, 0],
                    [0, 0, 1, 0, 0],
                    [0, 0, 0, 0, 0],
                ],
                dtype=float,
            )
        )
        score, labels, receipt = componentwise_eigenvector_centrality(adjacency)
        for component in set(labels[:4]):
            self.assertAlmostEqual(float(score[labels == component].sum()), 1.0)
        self.assertEqual(score[4], 0.0)
        self.assertEqual(receipt["isolates"], 1)

    def test_componentwise_eigenvector_nulls_unconverged_components(self):
        adjacency = sparse.csr_matrix(
            np.array([[0, 1, 1], [1, 0, 0], [1, 0, 0]], dtype=float)
        )
        score, _, receipt = componentwise_eigenvector_centrality(
            adjacency, tolerance=1e-15, max_iterations=1, allow_partial=True
        )
        self.assertEqual(receipt["status"], "PARTIAL_NULL")
        self.assertTrue(np.isnan(score).all())

    def test_support_combination_deduplicates_within_source(self):
        self.assertAlmostEqual(
            combine_independent_support([("a", 0.4), ("a", 0.7), ("b", 0.5)]),
            0.85,
        )

    def test_scoring_deduplicates_unordered_pair_within_layer(self):
        u, v, w, layer = deduplicate_undirected_layer_edges(
            [0, 1, 0, 0], [1, 0, 1, 1], [0.4, 0.8, 0.6, 1.0], [2, 2, 2, 3]
        )
        self.assertEqual(len(u), 2)
        self.assertEqual(sorted(w.tolist()), [0.8, 1.0])


class CanonicalGraphBuildTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
        p2 = root / "p2"
        lda = root / "lda"
        politico = root / "politico"
        candidates = root / "candidates"
        for directory in (p2, lda, politico, candidates):
            directory.mkdir()
        (p2 / "source_native_corpus_receipt.json").write_text("{}", encoding="utf-8")
        _write_rows(
            p2 / "littlesis_entities.ndjson.gz",
            [
                {"source_entity_id": "littlesis:entity:1", "primary_ext": "Person", "types": ["Person"], "name": "Alice A", "aliases": []},
                {"source_entity_id": "littlesis:entity:2", "primary_ext": "Org", "types": ["Organization"], "name": "Org A", "aliases": []},
            ],
        )
        _write_rows(
            p2 / "littlesis_relationship_assertions.ndjson.gz",
            [{
                "category_name": "Position", "category_id": 1,
                "subject_source_entity_id": "littlesis:entity:1",
                "object_source_entity_id": "littlesis:entity:2",
                "source_relationship_id": "littlesis:relationship:1",
                "description1": "Director", "description2": "Director",
                "source_is_current": False, "raw_start_date": "2020-00-00",
                "raw_end_date": None, "precision_bucket": "year",
                "source_updated_at": "2026-01-01T00:00:00Z", "temporal_weight": 0,
            }, {
                "category_name": "Position", "category_id": 1,
                "subject_source_entity_id": "littlesis:entity:2",
                "object_source_entity_id": "littlesis:entity:2",
                "source_relationship_id": "littlesis:relationship:self-loop",
                "description1": "source anomaly", "description2": "source anomaly",
                "source_is_current": False, "raw_start_date": None,
                "raw_end_date": None, "precision_bucket": "undated-only",
                "source_updated_at": "2026-01-01T00:00:00Z", "temporal_weight": 0,
            }],
        )
        _write_rows(
            p2 / "quorum_legacy_source_native_seed.ndjson",
            [{"source_entity_id": "L1", "source_system_id": "quorum-legacy-derived", "display_name": "Staff One", "title": "Chief of Staff", "member_office_source_key": "Jane Doe", "featured": False}],
        )

        (politico / "normalization_receipt.json").write_text("{}", encoding="utf-8")
        politico_person = "politico-pro-staffer-directory:person:p1"
        _write_rows(politico / "persons.ndjson", [{"source_person_id": politico_person, "display_name": "Lobby Person", "name_normalized": "lobby person", "party_partition": "Democratic"}])
        _write_rows(politico / "role_assertions.ndjson", [{"source_person_id": politico_person, "assertion_id": "politico:role:1", "role_text": "Chief of Staff for Rep. Jane DoeDemocraticCA-01", "observed_at": "2026-01-01T00:00:00Z"}])
        _write_rows(politico / "issue_assertions.ndjson", [{"source_person_id": politico_person, "assertion_id": "politico:issue:1", "issue_area": "Budget", "observed_at": "2026-01-01T00:00:00Z"}])

        (lda / "lda_source_native_corpus_receipt.json").write_text("{}", encoding="utf-8")
        _write_rows(lda / "lda_lobbyist_assertions.ndjson.gz", [{"source_person_id": "lda:lobbyist:10", "display_name": "Lobby Person", "filing_years": [2026], "activity_support_count": 1, "covered_position_support_count": 1}])
        _write_rows(lda / "lda_organization_assertions.ndjson.gz", [
            {"source_org_id": "lda:registrant:20", "display_name": "Registrant 202-555-0100", "organization_kind": "registrant"},
            {"source_org_id": "lda:client:30", "display_name": "Client", "organization_kind": "client"},
        ])
        _write_rows(lda / "lda_activity_assertions.ndjson.gz", [{"source_activity_id": "lda:activity:f:0", "general_issue_code_display": "Budget", "filing_uuid": "f", "filing_year": 2026, "filing_period": "first_quarter", "general_issue_code": "BUD", "dt_posted": "2026-01-02T00:00:00Z"}])
        industry = [
            ("NAMED_ON_ACTIVITY", "person", "lda:lobbyist:10", "lda_activity", "lda:activity:f:0"),
            ("FILED_BY", "lda_activity", "lda:activity:f:0", "registrant", "lda:registrant:20"),
            ("FOR_CLIENT", "lda_activity", "lda:activity:f:0", "client", "lda:client:30"),
        ]
        _write_rows(lda / "lda_industry_edges.ndjson.gz", [{"predicate": p, "subject_kind": sk, "subject_id": sid, "object_kind": ok, "object_id": oid, "edge_id": f"edge:{i}", "support_count": 1, "precision_bucket": "day"} for i, (p, sk, sid, ok, oid) in enumerate(industry)])
        _write_rows(lda / "lda_covered_position_assertions.ndjson.gz", [{"source_person_id": "lda:lobbyist:10", "assertion_id": "covered:1", "covered_position": "Former staff", "source_activity_id": "lda:activity:f:0", "filing_uuid": "f", "government_term_hits": ["staff"], "dt_posted": "2026-01-02T00:00:00Z"}])

        (candidates / "lda_identity_candidate_receipt.json").write_text("{}", encoding="utf-8")
        _write_rows(candidates / "lda_identity_candidates.ndjson.gz", [{
            "candidate_id": "candidate:1", "matched_source_system_id": "politico-pro-staffer-directory",
            "matched_source_entity_id": politico_person, "lda_source_person_id": "lda:lobbyist:10",
            "lda_name_collision_max": 1, "target_cross_source_multiplicity_max": 1,
            "target_name_collision_max": 1,
            "target_multiplicity_by_source_max": {"littlesis": 0, "politico-pro-staffer-directory": 1, "quorum-legacy-staffers": 0},
            "method_version": "fixture-v1", "match_evidence": [{"normalized_name": "lobby person"}],
            "source_binding_sha256": "a" * 64,
        }])

        legislators = root / "legislators-current.yaml"
        legislators.write_text(yaml.safe_dump([{
            "id": {"bioguide": "D000001"}, "name": {"first": "Jane", "last": "Doe", "official_full": "Jane Doe"},
            "terms": [{"type": "rep", "start": "2025-01-03", "end": "2027-01-03", "state": "CA", "district": 1, "party": "Democrat"}],
        }], sort_keys=True), encoding="utf-8")
        authority = root / "internal-analysis-authority.json"
        authority.write_text(
            json.dumps(
                {
                    "schema_id": "staffer-network-internal-analysis-authority-v1",
                    "generated_at": "2026-08-30T20:00:00Z",
                    "decision_authority": "test fixture",
                    "scope": "INTERNAL_ANALYSIS_ONLY",
                    "raw_redistribution_claimed": False,
                    "sources": {
                        source: {
                            "enabled_internal_analysis": True,
                            "authority_basis": "test fixture",
                            "evidence_sha256": "a" * 64,
                        }
                        for source in (
                            "congress-legislators",
                            "littlesis-bulk",
                            "politico-pro-staffer-directory",
                            "quorum-legacy-derived",
                            "senate-lda-api",
                        )
                    },
                }
            ),
            encoding="utf-8",
        )
        return p2, lda, politico, candidates, legislators, authority

    def test_real_builders_materialize_canonical_facts_and_scores(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            p2, lda, politico, candidates, legislators, authority = self._fixture(root)
            canonical_root = root / "canonical"
            receipt = build_canonical_generation(
                p2_root=p2, lda_root=lda, politico_root=politico,
                identity_candidates_root=candidates,
                legislators_current_path=legislators,
                analysis_authority_manifest_path=authority,
                output_root=canonical_root,
                generated_at="2026-08-30T20:00:00Z",
            )
            self.assertTrue(receipt["canonical_facts_materialized"])
            self.assertEqual(receipt["canonical_entity_merges"], 0)
            self.assertEqual(receipt["excluded_self_loop_facts"], 1)
            self.assertEqual(receipt["contact_redaction_counts"]["entity:senate-lda-api"], 1)
            self.assertEqual(receipt["contact_only_facts_excluded"], 0)
            self.assertEqual(receipt["undated_nonzero_temporal_weights"], 0)
            with gzip.open(
                canonical_root / "canonical_entities.ndjson.gz", "rt", encoding="utf-8"
            ) as stream:
                self.assertNotIn("202-555-0100", stream.read())
            with gzip.open(
                canonical_root / "canonical_facts.ndjson.gz", "rt", encoding="utf-8"
            ) as stream:
                facts = [json.loads(line) for line in stream]
            self.assertTrue(
                all(
                    fact["temporal_weight"] == 0.0
                    for fact in facts
                    if fact["precision_bucket"] == "undated-only"
                )
            )
            self.assertEqual(receipt["member_link_counts"]["politico_member_role_links"], 1)
            graph_root = root / "graph"
            graph = build_network_generation(
                canonical_root=canonical_root, output_root=graph_root,
                generated_at="2026-08-30T20:01:00Z",
            )
            self.assertEqual(graph["status"], "PASS")
            self.assertEqual(graph["identity_candidate_edges_included"], 1)
            self.assertTrue(graph["primary_scores_exclude_identity_candidates"])
            self.assertIsNone(graph["composite_influence_score"])
            with gzip.open(
                graph_root / "network_scores.ndjson.gz", "rt", encoding="utf-8"
            ) as stream:
                scores = [json.loads(line) for line in stream]
            self.assertEqual(
                graph["primary_isolates"],
                sum(score["primary_isolate"] for score in scores),
            )
            self.assertEqual(
                graph["exploratory_isolates"],
                sum(score["exploratory_isolate"] for score in scores),
            )
            schema_root = Path(__file__).parents[2] / "staffer_network" / "schemas"
            for name, schema_name in [
                ("canonical_generation_receipt.json", "canonical_generation_receipt.schema.json"),
                ("network_build_receipt.json", "network_build_receipt.schema.json"),
            ]:
                value = json.loads((canonical_root / name).read_text()) if name.startswith("canonical") else json.loads((graph_root / name).read_text())
                schema = json.loads((schema_root / schema_name).read_text())
                validate_instance(value, schema)

    def test_politico_activation_manifest_is_enabled_and_bound(self):
        repo = Path(__file__).parents[3]
        manifest = json.loads((repo / "data" / "staffer_network" / "policy" / "politico_pro_source_manifest_20260830_v3.json").read_text())
        self.assertTrue(manifest["enabled"])
        self.assertEqual(manifest["redistribution_class"], "public-safe-derived-only")
        raw = Path.home() / "AppData" / "Local" / "Melange" / "staffer_network" / "raw" / "politico" / "crawl-20260829T221300-0400"
        if raw.is_dir():
            validate_manifest_files(manifest, raw)


if __name__ == "__main__":
    unittest.main()
