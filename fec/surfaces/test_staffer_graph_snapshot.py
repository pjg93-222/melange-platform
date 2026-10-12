"""Run integration checks with STAFFER_GRAPH_TEST_SOURCE pointing at source B.

CoTenureStatusFilterTests (2026-09-07, ruling 2 / POSSIBLE_ONLY leak fix) needs no
such pinned source: it mints a complete, minimal, receipt-verified generation in a
temp tree -- the same binding ceremony StaticGraph._load() enforces on the real
one -- so the GUARANTEED-only co_tenure filter is exercised for real rather than
unit-tested against extracted logic.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from fec.surfaces.staffer_graph_snapshot import StaticGraph


class FailureTests(unittest.TestCase):
    def test_missing_verification_fails_closed_and_is_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = StaticGraph(directory)
            first = graph.view()
            second = graph.view()
            self.assertTrue(first["errors"])
            self.assertEqual(first["errors"], second["errors"])
            self.assertEqual(first["nodes"], [])
            self.assertEqual(first["edges"], [])


@unittest.skipUnless(os.environ.get("STAFFER_GRAPH_TEST_SOURCE"), "requires pinned verified source artifacts")
class VerifiedArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = StaticGraph(Path(os.environ["STAFFER_GRAPH_TEST_SOURCE"])).load()

    def test_layers_match_receipt_and_anchors_are_zero_weight(self):
        graph = self.graph
        self.assertEqual(graph.errors, [])
        counts = graph.layer_counts
        expected = graph.receipt["edge_counts"]
        self.assertEqual(counts["membership"], expected["membership"])
        self.assertEqual(counts["revolving"], expected["revolving"])
        self.assertEqual(counts["policy_area"], expected["policy_area_membership"])
        # Fix, 2026-09-07 (ruling 2): a co_tenure row's own status can be
        # POSSIBLE_ONLY, and this reader used to load every sampled row at full
        # weight regardless of that status. Post-fix, a co_tenure edge only
        # reaches graph.edges when GUARANTEED, so this no longer asserts a fixed
        # count of exactly SAMPLE_ROWS (that assumed every one of the first 10000
        # rows was GUARANTEED, which the real generation's own reported count of
        # 2,639,891 POSSIBLE_ONLY co_tenure rows says is not a safe assumption);
        # it asserts the filter actually held and that kept+dropped accounts for
        # every row read.
        co_tenure_edges = [edge for edge in graph.edges if edge["layer"] == "co_tenure"]
        self.assertEqual(counts["co_tenure"], len(co_tenure_edges))
        self.assertTrue(all(edge.get("status") == "GUARANTEED" for edge in co_tenure_edges))
        edges_source = next(s for s in graph.sources if s["role"] == "staffer_social_edges")
        status_filter = edges_source["co_tenure_status_filter"]
        self.assertEqual(status_filter["status_required"], "GUARANTEED")
        self.assertEqual(status_filter["kept"], len(co_tenure_edges))
        self.assertEqual(status_filter["kept"] + status_filter["dropped_non_guaranteed"], 10000)
        anchors = [edge for edge in graph.edges if edge["layer"] == "member_anchor"]
        self.assertTrue(anchors)
        self.assertTrue(all(edge["weight"] == 0 and edge["layout_only"] and not edge["operator_included"] for edge in anchors))
        self.assertTrue(all(source["verified"] for source in graph.sources))

    def test_named_regressions_keep_verified_identity_and_stints(self):
        for query, bios, stints in (("Gordon Holzberg", {"A000370", "S001223"}, 2), ("Stephanie Belk", {"V000127"}, 1)):
            view = self.graph.view(query, 40)
            people = [node for node in view["nodes"] if node["kind"] == "person"]
            self.assertEqual(len(people), 1)
            self.assertEqual(set(people[0]["member_ids"]), bios)
            self.assertEqual(len(people[0]["stints"]), stints)
            anchors = {node["member_id"] for node in view["nodes"] if node["kind"] == "member"}
            self.assertEqual(anchors, bios)
            self.assertFalse(view["canonical_eligible"])

    def test_every_review_assertion_remains_attached_without_operator_promotion(self):
        spec = next((f for f in self.graph.receipt['files']
                     if f['role'] == 'staffer_social_politico_review_layers'), None)
        if spec is None:
            self.skipTest('This historical generation predates the five review layers')
        assertions = [a for n in self.graph.nodes.values() for a in n.get('review_assertions', [])]
        assertions += [a for values in self.graph.pending_source_assertions.values() for a in values]
        self.assertEqual(len(assertions), spec['records'])
        self.assertEqual(len({a['assertion_id'] for a in assertions}), len(assertions))
        self.assertEqual(len({a['layer'] for a in assertions}), 5)
        self.assertTrue(all(a['artifact_path'] and not a['operator_included']
                            and not a['canonical_eligible'] for a in assertions))

    def test_member_name_search_uses_exact_bioguide_affiliation(self):
        for query, bio in (("Alma Adams", "A000370"), ("Emilia Sykes", "S001223")):
            view = self.graph.view(query, 1200)
            anchor = next(node for node in view["nodes"] if node["id"] == "member:" + bio)
            self.assertTrue(anchor["label_provenance"]["display_only"])
            self.assertTrue(anchor["label_provenance"]["raw_label"])
            staff = [node for node in view["nodes"] if node["kind"] == "person" and node["is_search_match"]]
            self.assertTrue(staff)
            self.assertTrue(any("Holzberg" in node["label"] for node in staff))
            self.assertTrue(all(bio in node["member_ids"] for node in staff))


def _write_gz(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")


def _sha256(path):
    handle = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            handle.update(block)
    return handle.hexdigest()


def _build_minimal_generation(root):
    """The smallest tree StaticGraph._load() will accept: two person nodes and
    four staffer_social_edges rows exercising every co_tenure status branch the
    2026-09-07 fix distinguishes (GUARANTEED, POSSIBLE_ONLY x2, and a row that
    omits status entirely -- fail-closed means that is dropped too, not treated
    as guaranteed by default). Every other layer this loader reads is emptied
    out on purpose: with zero real membership/policy/revolving edges, the
    receipt's own edge_counts of zero for each agree with what gets derived,
    so those unrelated gates stay satisfied without minting data this fix does
    not touch.
    """
    import fec.surfaces.staffer_graph_snapshot as snap

    generation = root / snap.GENERATION
    verification_dir = root / snap.VERIFICATION
    upstream = root / "upstream"

    _write_gz(generation / "staffer_social_node_scores.ndjson.gz", [
        {"node_id": "person:p1", "node_kind": "person"},
        {"node_id": "person:p2", "node_kind": "person"},
    ])
    _write_gz(generation / "staffer_trajectories.ndjson.gz", [])
    _write_gz(generation / "staffer_social_edges.ndjson.gz", [
        {"subject_id": "person:p1", "object_id": "person:p2", "layer": "co_tenure",
         "status": "GUARANTEED", "w_tiered_lower": 0.5, "edge_id": "e1"},
        {"subject_id": "person:p1", "object_id": "person:p2", "layer": "co_tenure",
         "status": "POSSIBLE_ONLY", "w_tiered_lower": 0.5, "edge_id": "e2"},
        {"subject_id": "person:p2", "object_id": "person:p1", "layer": "co_tenure",
         "status": "POSSIBLE_ONLY", "w_tiered_lower": 0.3, "edge_id": "e3"},
        {"subject_id": "person:p2", "object_id": "person:p1", "layer": "co_tenure",
         "w_tiered_lower": 0.1, "edge_id": "e4"},  # no status at all: fail closed, not GUARANTEED
    ])
    for name in ("hill_person_spine", "politico_direct_policy_memberships",
                 "exploratory_revolving_edges", "hill_member_office_index"):
        _write_gz(upstream / (name + ".ndjson.gz"), [])

    files = []
    for role, name in (("staffer_social_node_scores", "staffer_social_node_scores.ndjson.gz"),
                       ("staffer_trajectories", "staffer_trajectories.ndjson.gz"),
                       ("staffer_social_edges", "staffer_social_edges.ndjson.gz")):
        path = generation / name
        files.append({"role": role, "path": name, "sha256": _sha256(path)})
    inputs = []
    for role in ("hill_person_spine", "politico_direct_policy_memberships",
                "exploratory_revolving_edges", "hill_member_office_index"):
        path = upstream / (role + ".ndjson.gz")
        inputs.append({"role": role, "path": "upstream/" + role + ".ndjson.gz", "sha256": _sha256(path)})

    receipt = {
        "files": files, "inputs": inputs, "dials": {"s_strength": {}},
        "edge_counts": {"membership": 0, "policy_area_membership": 0,
                        "revolving": 0, "revolving_in_operator": 0},
    }
    receipt_path = generation / "staffer_social_network_receipt.json"
    generation.mkdir(parents=True, exist_ok=True)
    with open(receipt_path, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True)

    verification = {
        "status": "PASS", "failures": [],
        "twin_byte_identity": [{"path": "staffer_social_network_receipt.json",
                                "primary_sha256": _sha256(receipt_path)}],
    }
    verification_dir.mkdir(parents=True, exist_ok=True)
    with open(verification_dir / "staffer_social_network_verification_receipt.json",
             "w", encoding="utf-8") as stream:
        json.dump(verification, stream, sort_keys=True)


class CoTenureStatusFilterTests(unittest.TestCase):
    """Ruling 2, 2026-09-07: co_tenure edges are read with no status filter, so a
    POSSIBLE_ONLY row -- a candidate tie the generation itself declined to
    guarantee -- reaches the sidecar graph at full weight, same as a GUARANTEED
    one. Fixed at the read: only GUARANTEED co_tenure rows become edges, and the
    dropped count is recorded on the edges artifact's own provenance entry.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "staffer_network"
        _build_minimal_generation(self.root)
        self.graph = StaticGraph(self.root).load()

    def test_only_guaranteed_co_tenure_rows_become_edges(self):
        self.assertEqual(self.graph.errors, [])
        co_tenure_edges = [edge for edge in self.graph.edges if edge["layer"] == "co_tenure"]
        self.assertEqual(len(co_tenure_edges), 1)
        self.assertEqual(co_tenure_edges[0]["provenance"]["edge_id"], "e1")
        self.assertEqual(co_tenure_edges[0]["status"], "GUARANTEED")
        self.assertEqual(self.graph.layer_counts.get("co_tenure"), 1)

    def test_the_dropped_count_is_recorded_on_the_edges_artifact_provenance(self):
        edges_source = next(s for s in self.graph.sources if s["role"] == "staffer_social_edges")
        self.assertEqual(edges_source["co_tenure_status_filter"],
                         {"status_required": "GUARANTEED", "kept": 1, "dropped_non_guaranteed": 3})

    def test_a_row_with_no_status_at_all_is_dropped_not_treated_as_guaranteed(self):
        # e4 carries no "status" key. Fail-closed means absence is never read as
        # GUARANTEED, so it counts as dropped alongside the two explicit
        # POSSIBLE_ONLY rows (e2, e3) -- three dropped, matching the provenance
        # count above, not two.
        kept_ids = {edge["provenance"]["edge_id"] for edge in self.graph.edges if edge["layer"] == "co_tenure"}
        self.assertEqual(kept_ids, {"e1"})


if __name__ == "__main__":
    unittest.main()
