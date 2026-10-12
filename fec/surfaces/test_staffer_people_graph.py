from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fec.surfaces.staffer_graph_snapshot import StaticGraph
from fec.surfaces.staffer_people_graph import project_people
from fec.surfaces.staffer_routing import AdvocacyRouter


class PeopleProjectionTests(unittest.TestCase):
    def setUp(self):
        self.static = StaticGraph("fixture")
        self.static._loaded = True
        self.static.nodes = {
            "gordon": {"id": "gordon", "kind": "person", "label": "Gordon Holzberg", "member_ids": [], "provenance": {"large": "x" * 1000}},
            "stephanie": {"id": "stephanie", "kind": "person", "label": "Stephanie Belk", "member_ids": []},
            "greg": {"id": "greg", "kind": "person", "label": "Greg Mesack", "member_ids": []},
            "firm": {"id": "firm", "kind": "organization", "label": "Evidence Firm", "member_ids": []},
            "committee": {"id": "committee", "kind": "container", "label": "A Committee", "member_ids": []},
            "member:A000370": {"id": "member:A000370", "kind": "member", "label": "Alma Adams", "member_ids": ["A000370"]}}
        self.static.reindex()
        self.episodes = [{"person_id": person, "employer_id": "firm", "start": "2020-01-01", "end_exclusive": "2021-01-01",
                          "provenance": {"large_evidence": "x" * 1000}} for person in ("gordon", "stephanie")]
        self.episodes.extend({"person_id": person, "employer_id": "activity:source-id", "employer_label": "Evidence Firm",
                              "start": "2026-04-01", "end_exclusive": "2026-07-01", "relationship": "co_reported_lobbying_activity"}
                             for person in ("gordon", "greg"))
        self.router = AdvocacyRouter(self.static.nodes, self.episodes, "fixture", "2026-09-04")

    def test_only_people_with_direct_evidence_edges_no_hubs(self):
        view = project_people(self.static, self.router, "Gordon", 30)
        self.assertEqual({node["id"] for node in view["nodes"]}, {"gordon", "stephanie", "greg"})
        self.assertTrue(all(node["kind"] == "person" for node in view["nodes"]))
        self.assertEqual({edge["relationship"] for edge in view["edges"]}, {"overlapped_tenure", "co_reported_lobbying_activity"})
        self.assertTrue(all(edge["source"] != "firm" and edge["target"] != "firm" for edge in view["edges"]))
        self.assertTrue(all(edge["provenance_available_on_selection"] for edge in view["edges"]))
        self.assertTrue(all("provenance" not in node for node in view["nodes"]))
        self.assertTrue(all("provenance" not in edge for edge in view["edges"]))
        self.assertEqual(next(edge for edge in view["edges"] if edge["target"] == "greg")["employer_label"], "Evidence Firm")

    def test_people_limit_and_missing_or_institution_queries(self):
        view = project_people(self.static, self.router, "Gordon", 1)
        self.assertEqual(len(view["nodes"]), 1)
        self.assertEqual(view["edges"], [])
        self.assertEqual(project_people(self.static, self.router, "Evidence Firm")["nodes"], [])
        self.assertEqual(project_people(self.static, self.router, "no-such-name")["nodes"], [])

    def test_nonoverlap_does_not_become_a_display_relationship(self):
        self.episodes[1].update(start="2021-01-01", end_exclusive="2022-01-01")
        router = AdvocacyRouter(self.static.nodes, self.episodes, "fixture2", "2026-09-04")
        view = project_people(self.static, router, "Gordon")
        self.assertNotIn("stephanie", {node["id"] for node in view["nodes"]})

    def test_confirmed_alias_and_live_source_overlay_are_exact(self):
        live = {"gordon": {**self.static.nodes["gordon"], "label": "Gordon Verified Name", "verification": "PROFILE_ACCEPTED"}}
        view = project_people(self.static, self.router, "old:gordon", 20, live, {"old:gordon": "gordon"})
        self.assertEqual(view["nodes"][0]["id"], "gordon")
        self.assertEqual(view["nodes"][0]["label"], "Gordon Verified Name")
        self.assertNotIn("old:gordon", {node["id"] for node in view["nodes"]})

    def test_default_named_seeds_and_hard_neighbor_query_bound(self):
        calls = []
        def limited(person, options):
            calls.append(person)
            return {"search_limited": True}
        router = SimpleNamespace(nodes=self.static.nodes, activity_provider=None,
                                 coworkers=limited, reported_collaborators=limited)
        view = project_people(self.static, router)
        self.assertIn("gordon", {node["id"] for node in view["nodes"]})
        self.assertLessEqual(len(calls), 16)
        self.assertTrue(view["bounds"]["search_limited"])
        self.assertLessEqual(view["bounds"]["queried_people"], 8)

    def test_every_seed_is_projected_with_a_fixed_budget_regardless_of_the_clock(self):
        calls = []

        def hops(person, options):
            calls.append((options["timeout_ms"], options["max_neighbor_checks"]))
            return {"status": "ok", "search_limited": False, "neighbor_checks": 3, "collaborators": [],
                    "coworkers": [{"source": person, "target": "stephanie", "relationship": "overlapped_tenure",
                                   "cost": 3, "employer_id": "firm", "employer_label": "Evidence Firm"}]}
        router = SimpleNamespace(nodes=self.static.nodes, activity_provider=None, coworkers=hops, reported_collaborators=hops)
        clock = [1000.0]

        def slow_monotonic():
            clock[0] += .3  # every clock read costs more than the whole old 0.5 s projection budget
            return clock[0]
        with patch("fec.surfaces.staffer_people_graph.time", SimpleNamespace(monotonic=slow_monotonic)):
            first = project_people(self.static, router)
            second = project_people(self.static, router)
        self.assertEqual(first["bounds"]["queried_people"], 3)  # gordon, stephanie and greg are all projected
        self.assertFalse(first["bounds"]["truncated"])
        self.assertTrue(first["bounds"]["neighborhoods_complete"])
        self.assertEqual(set(calls), {(1500, 8000)})
        self.assertEqual(first["nodes"], second["nodes"])
        self.assertEqual(first["edges"], second["edges"])
        self.assertEqual({edge["target"] for edge in first["edges"]}, {"stephanie"})

    def test_a_clock_cut_is_reported_as_truncated_and_never_as_complete(self):
        def cut(person, options):
            return {"status": "search_limited", "search_limited": True, "neighbor_checks": 12}
        router = SimpleNamespace(nodes=self.static.nodes, activity_provider=None, coworkers=cut, reported_collaborators=cut)
        bounds = project_people(self.static, router)["bounds"]
        self.assertTrue(bounds["truncated"])
        self.assertTrue(bounds["time_limited"])
        self.assertFalse(bounds["neighborhoods_complete"])

        def exhausted(person, options):
            return {"status": "search_limited", "search_limited": True, "neighbor_checks": options["max_neighbor_checks"] + 1}
        router = SimpleNamespace(nodes=self.static.nodes, activity_provider=None, coworkers=exhausted, reported_collaborators=exhausted)
        bounds = project_people(self.static, router)["bounds"]
        self.assertTrue(bounds["truncated"])
        self.assertFalse(bounds["time_limited"])  # the effort bound is deterministic; only a clock cut is transient
        self.assertFalse(bounds["neighborhoods_complete"])

    def test_node_limit_is_one_shared_constant(self):
        from fec.surfaces.staffer_people_graph import NODE_LIMIT, SEED_LIMIT
        view = project_people(self.static, self.router, "Gordon", 1800)
        self.assertEqual(view["bounds"]["node_limit"], NODE_LIMIT)
        self.assertEqual(view["bounds"]["seed_limit"], SEED_LIMIT)
        self.assertEqual(view["bounds"]["node_limit"], project_people(self.static, self.router, "Gordon", 500)["bounds"]["node_limit"])


if __name__ == "__main__":
    unittest.main()
