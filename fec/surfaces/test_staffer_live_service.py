"""Small in-memory contract fixtures: no crawl files or model artifacts required."""
from datetime import datetime, timezone, timedelta
from copy import deepcopy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fec.surfaces.staffer_graph_snapshot import StaticGraph
from fec.surfaces.staffer_live_service import LiveService


class ProfilesFixture:
    raw_dir = Path("fixture/crawl")
    accepted_prefix_sha256 = "prefix-one"
    accepted_receipt_digest = "receipt-one"

    def __init__(self):
        self.accepted = []
        self.nodes = []
        self.metadata = {"coverage": {"verified": 0}, "errors": [], "freshness": {"stale": False}}

    def refresh(self):
        return self.metadata

    @staticmethod
    def eligible(row):
        return True

    def graph(self, query, limit):
        return {"nodes": self.nodes, "edges": []}


def fixture_graph():
    graph = StaticGraph("fixture")
    graph._loaded = True
    graph.load_seconds = 0
    graph.verification = {"primary": "fixture-v4", "status": "PASS"}
    graph.sources = [{"role": "fixture", "sha256": "verified-source", "verified": True}]
    names = {"person:gordon": "Gordon Holzberg", "person:stephanie": "Stephanie Belk",
             "person:other-gordon": "Gordon Holzberg", "member:A000370": "Alma Adams"}
    for node_id, label in names.items():
        person = node_id.startswith("person:")
        graph.nodes[node_id] = {"id": node_id, "kind": "person" if person else "member", "label": label,
            "member_ids": [] if person else ["A000370"], "committee_ids": [], "policy_ids": [],
            "member_id": None if person else "A000370", "identity_tier": "S1",
            "provenance": {"source_row": node_id, "heavy_blob": "x" * 4000},
            "stints": [], "aliases": [], "canonical_eligible": False}
    for node_id in ("person:gordon", "person:stephanie"):
        graph.nodes[node_id]["stints"] = [{"container_id": "office:test", "core_start": "2020-01-01",
            "core_end_exclusive": "2021-01-01", "container_kind": "member_office", "date_precision": "DAY"}]
    graph.edges = [{"id": "sample-edge", "source": "person:gordon", "target": "person:stephanie",
                    "layer": "co_tenure", "weight": .5, "provenance": {"heavy_edge_blob": "y" * 4000}}]
    graph.adjacency["person:gordon"] = [0]
    graph.adjacency["person:stephanie"] = [0]
    graph.reindex()
    return graph


class LiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.graph = fixture_graph()
        self.service = LiveService("fixture", self.graph)
        self.service.profiles = ProfilesFixture()
        self.service._refresh_profiles(force=True)
        self.service.ready = True
        self.service.stage = "Ready"

    def test_readiness_does_not_promote_source_exceptions_or_identities(self):
        self.service.metadata['coverage']['completion_status'] = 'COMPLETE_WITH_EXCEPTIONS'
        value = self.service.overview_status()
        self.assertEqual(value['status'], 'READY_EXPLORATORY')
        self.assertEqual(value['collection_completion_status'], 'COMPLETE_WITH_EXCEPTIONS')
        self.assertEqual(value['graph_verification_status'], 'PASS')
        self.assertFalse(value['terminal_verified'])
        self.service.metadata['errors'] = ['source changed']
        self.assertEqual(self.service.overview_status()['status'], 'READY_WITH_ERRORS')
        self.service.ready = False
        self.assertEqual(self.service.overview_status()['status'], 'IN_PROGRESS')

    def test_shared_employer_context_never_asserts_temporal_overlap_and_reports_caps(self):
        self.service.profile_nodes = {
            str(i): {'id': str(i), 'kind': 'person', 'label': f'Person {i}',
                     'employment_history': [{'employer': 'Large employer'}]}
            for i in range(251)}
        self.service.profile_nodes['0']['employment_history'].append({'employer': 'Earlier employer', 'start': '1980', 'end': '1981'})
        self.service.profile_nodes['1']['employment_history'].append({'employer': 'Earlier employer', 'start': '2020', 'end': '2021'})
        result = self.service.full_network()
        self.assertEqual(len(result['nodes']), 251)
        self.assertEqual(result['full_network']['large_groups_skipped'], 1)
        self.assertFalse(result['full_network']['edges_complete_for_shared_employer_layers'])
        self.assertTrue(result['bounds']['search_limited'])
        self.assertFalse(result['bounds']['complete_network'])
        self.assertEqual(len(result['edges']), 1)
        edge = result['edges'][0]
        self.assertEqual(edge['relationship'], 'shared_historical_employer')
        self.assertFalse(edge['temporal_overlap_established'])
        self.assertFalse(edge['routing_eligible'])
        self.assertNotIn('cost', edge)
        self.service.metadata['errors'] = ['new source error']
        self.assertEqual(self.service.full_network()['status'], 'READY_WITH_ERRORS')

    def test_compact_snapshot_but_full_person_and_edge_provenance(self):
        snapshot = self.service.snapshot("Gordon Holzberg", 20)
        self.assertTrue(snapshot["nodes"])
        self.assertTrue(all("provenance" not in node and "stints" not in node for node in snapshot["nodes"]))
        self.assertTrue(all("provenance" not in edge for edge in snapshot["edges"]))
        person = self.service.person("person:gordon")
        self.assertIn("heavy_blob", person["node"]["provenance"])
        self.assertIn("heavy_edge_blob", person["edges"][0]["provenance"])
        self.assertEqual(person["coworkers"][0]["node"]["id"], "person:stephanie")

    def test_person_pages_are_independent_and_affiliations_survive_edge_sampling(self):
        original = self.graph.edges[0]
        for i in range(105):
            self.graph.adjacency['person:gordon'].append(len(self.graph.edges))
            self.graph.edges.append({**original, 'id': f'sample-{i}'})
        self.graph.adjacency['person:gordon'].append(len(self.graph.edges))
        self.graph.edges.append({'id': 'office-proof', 'source': 'person:gordon', 'target': 'member:A000370', 'layer': 'membership', 'weight': 0})
        person = self.service.person('person:gordon', {'coworker_offset': 1, 'collaborator_offset': 0, 'limit': 1})
        self.assertEqual(person['coworkers'], [])
        self.assertEqual(person['pagination']['coworkers']['total'], 1)
        self.assertEqual(person['pagination']['coworkers']['offset'], 1)
        self.assertEqual(person['pagination']['reported_collaborators']['offset'], 0)
        self.assertEqual([r['id'] for r in person['node']['affiliation_evidence']], ['office-proof'])

    def test_encoded_response_cache_and_etag_are_stable(self):
        first = self.service.encoded_snapshot("Gordon Holzberg", 20)
        second = self.service.encoded_snapshot("Gordon Holzberg", 20)
        self.assertIs(first, second)
        self.assertEqual(first[1], second[1])
        self.assertTrue(json.loads(gzip.decompress(first[0]))["ready"])

    def test_metadata_refresh_preserves_graph_revision_and_invalidates_bytes(self):
        first = self.service.snapshot("Gordon", 20)
        encoded = self.service.encoded_snapshot("Gordon", 20)
        router = self.service.router
        self.service.profiles.metadata = {"coverage": {"verified": 0}, "errors": ["source stale"], "freshness": {"stale": True}}
        self.service._refresh_profiles()
        second = self.service.snapshot("Gordon", 20, since=first["revision"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertNotEqual(first["metadata_revision"], second["metadata_revision"])
        self.assertEqual(second["nodes"], [])
        self.assertIn("source stale", second["errors"])
        self.assertIs(router, self.service.router)
        self.assertNotEqual(encoded[1], self.service.encoded_snapshot("Gordon", 20)[1])

    def test_verified_prefix_change_rebuilds_graph_and_router_revision(self):
        first = self.service.snapshot("Gordon", 20)
        router = self.service.router
        self.service.profiles.accepted_prefix_sha256 = "prefix-two"
        self.service.profiles.nodes = [{"id": "profile:gordon-distinct", "kind": "person", "label": "Gordon Holzberg",
            "member_ids": [], "committee_ids": [], "policy_ids": []}]
        self.service.profiles.accepted = [{}]
        self.service._refresh_profiles()
        second = self.service.snapshot("Gordon", 20)
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertIsNot(router, self.service.router)
        self.assertIn("profile:gordon-distinct", {node["id"] for node in second["nodes"]})

    def test_name_prefix_search_keeps_same_named_identities_separate(self):
        result = self.service.search("Gor Holz", 20)
        ids = {node["id"] for node in result["nodes"]}
        self.assertEqual(ids, {"person:gordon", "person:other-gordon"})
        self.assertEqual(self.graph.nodes["person:gordon"]["provenance"]["source_row"], "person:gordon")

    def test_exact_mixed_case_id_search(self):
        self.assertEqual(self.graph.search_ids("member:A000370"), ["member:A000370"])
        self.assertEqual(self.service.search("member:A000370")["nodes"], [])
        ident = 'person:Mixed-UUID'
        self.graph.nodes[ident] = {'id': ident, 'label': 'Mixed Case Person', 'kind': 'person'}
        self.graph.reindex()
        self.assertEqual(self.service.search(ident)['nodes'][0]['id'], ident)

    def test_snapshot_and_search_exclude_institutions(self):
        self.graph.nodes['organization:test'] = {'id': 'organization:test', 'kind': 'organization', 'label': 'Gordon Institute'}
        self.graph.reindex()
        self.assertTrue(all(n['kind'] == 'person' for n in self.service.search('Gordon')['nodes']))
        graph = self.service.snapshot('Gordon')
        self.assertTrue(all(n['kind'] == 'person' for n in graph['nodes']))
        ids = {n['id'] for n in graph['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in graph['edges']))

    def test_exact_profile_id_search(self):
        self.service.profiles.nodes = [{"id": "profile:Distinct-UUID", "kind": "person", "label": "Separate Name",
            "member_ids": [], "committee_ids": [], "policy_ids": []}]
        self.service.profiles.accepted = [{"profile": "Distinct-UUID"}]
        self.service._refresh_profiles()
        self.assertEqual([node["id"] for node in self.service.search("profile:Distinct-UUID")["nodes"]], ["profile:Distinct-UUID"])

    def test_verified_profile_overlays_same_uuid_directory_placeholder_once(self):
        node_id = "politico:shared-uuid"
        self.graph.nodes[node_id] = {"id": node_id, "kind": "person", "label": "Directory Person",
            "member_ids": [], "committee_ids": [], "policy_ids": [], "profile_pending": True,
            "verification": "SOURCE_DIRECTORY_VERIFIED_PROFILE_PENDING", "provenance": {"directory": True}}
        self.graph.reindex()
        self.service.profiles.nodes = [{"id": node_id, "kind": "person", "label": "Directory Person",
            "member_ids": [], "committee_ids": [], "policy_ids": [],
            "verification": "COLLECTION_VERIFIED_REVIEW_ONLY", "provenance": {"verified_profile": True},
            "employment_history": [{"employer": "Evidence Employer"}]}]
        self.service.profiles.accepted = [{"profile_uuid": "shared-uuid"}]
        self.service._refresh_profiles()
        person = self.service.person(node_id)
        self.assertTrue(person["node"]["provenance"].get("verified_profile"))
        self.assertEqual(person["node"]["employment_history"][0]["employer"], "Evidence Employer")
        found = self.service.search("Directory Person", 20)["nodes"]
        self.assertEqual([node["id"] for node in found].count(node_id), 1)
        shown = self.service.snapshot("Directory Person", 20)["nodes"]
        self.assertEqual([node["id"] for node in shown].count(node_id), 1)

    def test_route_adapter_has_clickable_identifiers_labels_and_provenance(self):
        route = self.service.route("person:gordon", "person:stephanie")
        self.assertTrue(route["found"])
        self.assertEqual(route["path"], ["person:gordon", "person:stephanie"])
        self.assertEqual({node["id"] for node in route["nodes"]}, set(route["path"]))
        self.assertEqual(route["hops"][0]["source_label"], "Gordon Holzberg")
        self.assertTrue(route["hops"][0]["id"].startswith("route:"))
        self.assertTrue(route["hops"][0]["provenance"])
        self.assertTrue(route["graph"]["nodes"])
        self.assertFalse(route["canonical_weights_changed"])

    def test_route_endpoints_receive_identity_priority(self):
        calls = []
        self.service.identity_web = SimpleNamespace(prioritize_people=lambda nodes: calls.append(set(nodes)))
        self.service.route('person:gordon', 'person:stephanie')
        self.assertEqual(calls, [{'person:gordon', 'person:stephanie'}])

    def test_pending_assertion_blocks_old_routes_even_if_identity_worker_finishes(self):
        self.service._assertion_update_pending = True
        self.service.ready = True
        self.assertEqual(self.service.route('person:gordon', 'person:stephanie')['status'], 'loading')
        self.assertFalse(self.service.person('person:gordon')['ready'])

    def test_unknown_and_unreachable_are_distinct(self):
        self.assertEqual(self.service.route("missing", "person:gordon")["status"], "unknown_person")
        self.assertEqual(self.service.route("person:gordon", "person:other-gordon")["status"], "unreachable")
        self.assertEqual(self.service.person("missing")["status"], "unknown_person")

    def test_local_rollover_refreshes_routing_as_of_without_new_profiles(self):
        with patch("fec.surfaces.staffer_live_service.local_date") as clock:
            clock.return_value = "2026-09-04"
            self.service._refresh_profiles(force=True)
            first_revision = self.service.revision
            clock.return_value = "2026-09-05"
            self.service._refresh_profiles()
        self.assertEqual(self.service.router.as_of, "2026-09-05")
        self.assertNotEqual(first_revision, self.service.revision)

    def test_user_confirmed_alias_shares_search_detail_and_dated_route(self):
        primary, alias = 'person:lda:lobbyist:154321', 'person:lda:lobbyist:68016'
        node = {'id': primary, 'label': 'Kathleen Coulombe', 'kind': 'person',
                'member_ids': [], 'committee_ids': [], 'policy_ids': [],
                'identity_status': 'USER_CONFIRMED_SAME_PERSON',
                'source_person_ids': ['lda:lobbyist:154321', 'lda:lobbyist:68016'],
                'identity_provenance': {'source': 'user_statement'}}
        self.graph.nodes[alias] = {**node, 'id': alias}
        self.service.inventory = {'nodes': {primary: node}, 'identity_aliases': {alias: primary},
                                  'identity_groups': [{'primary_id': primary}]}
        self.service.evidence = {'episodes': [{'person_id': alias, 'employer_id': 'office:test',
            'start': '2020-01-01', 'end_exclusive': '2021-01-01', 'provenance': {'source_row': alias}}]}
        self.service._apply_inventory()
        self.service._refresh_profiles(force=True)
        self.assertNotIn(alias, self.graph.nodes)
        self.assertEqual([n['id'] for n in self.service.search('Kathleen Coulombe')['nodes']], [primary])
        self.assertEqual(self.service.search(alias)['nodes'][0]['id'], primary)
        self.assertEqual(self.service.person(alias)['node']['id'], primary)
        self.assertEqual(self.service.snapshot(alias)['nodes'][0]['id'], primary)
        route = self.service.route('person:gordon', alias)
        self.assertTrue(route['found'])
        self.assertEqual(route['path'], ['person:gordon', primary])
        self.assertTrue(any('confirmed by you' in warning for warning in route['warnings']))
        self.assertEqual(self.service.evidence['episodes'][0]['provenance']['original_person_id'], alias)

    def test_alias_merge_remaps_real_edges_without_changing_source_weights_or_cached_views(self):
        primary, alias, third = "person:gordon", "person:other-gordon", "person:stephanie"
        self.graph.edges.extend([
            {"id": "alias-to-third", "source": alias, "target": third, "layer": "co_tenure", "weight": .25,
             "provenance": {"source_record": "retained"}},
            {"id": "primary-to-alias", "source": primary, "target": alias, "layer": "co_tenure", "weight": .75,
             "provenance": {"source_record": "self-loop-after-display-merge"}}])
        self.graph.adjacency[alias] = [1, 2]
        self.graph.adjacency[third] = [0, 1]
        self.graph.adjacency[primary] = [0, 2]
        self.service._source_nodes = deepcopy(self.graph.nodes)
        self.service._source_edges = deepcopy(self.graph.edges)
        self.service._source_episodes = []
        original_edges = deepcopy(self.service._source_edges)
        before = self.service.snapshot(alias, 20)
        before_bytes = self.service.encoded_snapshot(alias, 20)
        self.service.search(alias)
        self.service.route(alias, third)
        merged = {**self.graph.nodes[primary], "identity_status": "USER_CONFIRMED_SAME_PERSON",
                  "identity_alias_ids": [alias], "identity_provenance": {"source": "fixture_user_confirmation"},
                  "source_summary": "Two source identities, explicitly confirmed by user"}
        self.service.inventory = {"nodes": {primary: merged}, "identity_aliases": {alias: primary},
            "identity_groups": [{"primary_id": primary}], "sources": [{"sha256": "new-fixture-user-overlay"}]}
        self.service._apply_inventory()
        self.service._refresh_profiles(force=True)
        after = self.service.snapshot(alias, 20)
        self.assertNotEqual(before["revision"], after["revision"])
        self.assertNotEqual(before_bytes[1], self.service.encoded_snapshot(alias, 20)[1])
        self.assertEqual(self.service._source_edges, original_edges)
        self.assertEqual(len(self.graph.edges), 2)
        remapped = next(edge for edge in self.graph.edges if edge["id"] == "alias-to-third")
        self.assertEqual((remapped["source"], remapped["target"], remapped["weight"]), (primary, third, .25))
        self.assertEqual(remapped["provenance"]["original_endpoints"], [alias, third])
        self.assertNotIn(alias, self.graph.adjacency)
        self.assertTrue(all(self.graph.edges[i]["source"] == primary or self.graph.edges[i]["target"] == primary
                            for i in self.graph.adjacency[primary]))
        self.assertEqual(self.service.search(alias)["nodes"][0]["id"], primary)
        self.assertEqual(self.service.person(alias)["node"]["id"], primary)
        self.assertEqual(self.service.route(alias, third)["path"], [primary, third])
        shown = next(node for node in after["nodes"] if node["id"] == primary)
        self.assertEqual(shown["source_summary"], merged["source_summary"])
        self.assertEqual(shown["label"], "Gordon Holzberg")
        self.assertTrue(all(node["kind"] == "person" for node in after["nodes"]))
        self.assertTrue(all(edge["source"] != alias and edge["target"] != alias for edge in after["edges"]))

    def test_identity_decision_requires_displayed_candidates_and_blocks_loading_reads(self):
        primary, alias = "person:gordon", "person:other-gordon"
        with patch("fec.surfaces.staffer_live_service.record_identity_decision") as persist:
            with self.assertRaisesRegex(ValueError, "displayed identity review"):
                self.service.identity_decision(primary, [alias], "merge")
            persist.assert_not_called()
            self.service.candidates_by_person = {primary: [{"node_ids": [primary, alias]}]}
            persist.return_value = {"decision": "merge"}
            self.service.encoded_snapshot("Gordon", 20)
            with patch("fec.surfaces.staffer_live_service.threading.Thread") as background:
                result = self.service.identity_decision(primary, [alias], "merge")
                self.assertEqual(result["status"], "reindexing")
                background.return_value.start.assert_called_once()
            self.assertFalse(self.service.ready)
            self.assertFalse(self.service.snapshot()["ready"])
            self.assertFalse(self.service.person(alias)["ready"])
            self.assertEqual(self.service.route(primary, alias)["status"], "loading")
            self.assertIn(alias, self.graph.nodes)
            persist.assert_called_once()

    def test_current_user_assertion_survives_confirmed_alias_remap(self):
        primary, alias, other = "person:gordon", "person:other-gordon", "person:stephanie"
        self.service.identity_aliases = {alias: primary}
        self.graph.nodes.pop(alias)
        self.service.evidence["user_assertions"] = [{"person_ids": [alias, other], "as_of": "2026-09-04",
                                                     "provenance": {"source": "fixture_user_statement"}}]
        episodes = self.service._user_episodes()
        self.assertEqual({episode["person_id"] for episode in episodes}, {primary, other})
        with patch("fec.surfaces.staffer_live_service.local_date", return_value="2026-09-04"), patch(
                "fec.surfaces.staffer_live_service.load_coworker_assertions", return_value=self.service.evidence['user_assertions']):
            self.service._refresh_profiles(force=True)
        route = self.service.route(alias, other, {"include_user_asserted": True, "objective": "evidence_cost"})
        self.assertEqual(route["path"], [primary, other])
        self.assertEqual(route["cost"], 1)
        self.assertTrue(route["hops"][0]["user_asserted"])
        self.assertEqual(self.service.route(alias, other, {"objective": "evidence_cost"})["cost"], 3)

    def test_identity_note_and_basis_name_the_groups_own_proof_kind(self):
        self.graph.nodes["person:fourth"] = {"id": "person:fourth", "kind": "person", "label": "Fourth Person",
            "member_ids": [], "committee_ids": [], "policy_ids": []}
        self.graph.reindex()
        groups = [
            {"primary_id": "person:gordon", "status": "REFERENCE_RESOLVED_SAME_PERSON", "basis": "RECORD_RESOLVED",
             "provenance": {"basis": "RECORD_RESOLVED", "proof_kinds": ["record"], "citations": []}},
            {"primary_id": "person:stephanie", "status": "REFERENCE_RESOLVED_SAME_PERSON",
             "provenance": {"basis": "REFERENCE_RESOLVED", "proof_kinds": ["reference"], "citations": [{"url": "https://example.org/bio"}]}},
            {"primary_id": "person:other-gordon", "status": "REFERENCE_RESOLVED_SAME_PERSON",
             "basis": "RECORD_AND_REFERENCE_RESOLVED", "provenance": {}},
            {"primary_id": "person:fourth", "status": "USER_CONFIRMED_SAME_PERSON", "provenance": {"source": "user_statement"}}]
        nodes = {g["primary_id"]: {**self.graph.nodes[g["primary_id"]], "identity_status": g["status"]} for g in groups}
        self.service.inventory = {"nodes": nodes, "identity_aliases": {}, "identity_groups": groups}
        self.service._apply_inventory()
        self.service._refresh_profiles(force=True)
        labelled = {i: (n["identity_basis"], n["identity_note"]) for i, n in self.graph.nodes.items() if "identity_basis" in n}
        self.assertEqual(labelled["person:gordon"][0], "RECORD_RESOLVED")
        self.assertIn("verified source records", labelled["person:gordon"][1])
        self.assertNotIn("references", labelled["person:gordon"][1])
        self.assertEqual(labelled["person:stephanie"], ("REFERENCE_RESOLVED",
            "Identity matched from references; source IDs and dated evidence are retained"))
        self.assertEqual(labelled["person:other-gordon"][0], "RECORD_AND_REFERENCE_RESOLVED")
        self.assertIn("records and references", labelled["person:other-gordon"][1])
        self.assertEqual(labelled["person:fourth"], ("USER_CONFIRMED",
            "Identity confirmed by you; both source IDs and their dated evidence are retained"))
        shown = self.service.search("Gordon Holzberg")["nodes"]
        self.assertEqual({n["id"]: n["identity_basis"] for n in shown},
                         {"person:gordon": "RECORD_RESOLVED", "person:other-gordon": "RECORD_AND_REFERENCE_RESOLVED"})
        route = self.service.route("person:gordon", "person:stephanie")
        self.assertTrue(route["found"])
        self.assertTrue(any("verified source records" in w for w in route["warnings"]))
        self.assertTrue(any("supported by references" in w for w in route["warnings"]))
        from fec.surfaces.staffer_live_service import group_identity_basis
        # A legacy group that carries no basis keeps the label it always had.
        self.assertEqual(group_identity_basis({"status": "REFERENCE_RESOLVED_SAME_PERSON", "provenance": {}}), "REFERENCE_RESOLVED")

    def test_route_warning_names_record_evidence_and_not_references(self):
        group = {"primary_id": "person:gordon", "status": "REFERENCE_RESOLVED_SAME_PERSON", "basis": "RECORD_RESOLVED",
                 "provenance": {"basis": "RECORD_RESOLVED", "citations": []}}
        self.service.inventory = {"nodes": {"person:gordon": {**self.graph.nodes["person:gordon"],
            "identity_status": "REFERENCE_RESOLVED_SAME_PERSON"}}, "identity_aliases": {}, "identity_groups": [group]}
        self.service._apply_inventory()
        self.service._refresh_profiles(force=True)
        route = self.service.route("person:gordon", "person:stephanie")
        self.assertTrue(route["found"])
        self.assertTrue(any("verified source records" in w for w in route["warnings"]))
        self.assertFalse(any("supported by references" in w for w in route["warnings"]))

    def test_totals_count_people_and_person_to_person_edges_only(self):
        self.graph.receipt = {"edge_counts": {"membership": 10, "policy_area_membership": 5, "revolving": 2,
            "co_tenure_guaranteed": 100, "co_tenure_possible_only_zero_weight": 20, "total_rows_emitted": 137},
            "node_universe": {"person": 50, "container": 2, "organization": 1, "total": 53}}
        self.graph.source_node_count = 53
        for payload in (self.service.snapshot("Gordon", 20), self.service.overview_status()):
            totals = payload["totals"]
            self.assertEqual((totals["nodes"], totals["edges"]), (50, 120))
            self.assertEqual(totals["source_nodes_including_institutions"], 53)
            self.assertEqual(totals["source_edges_including_institution_links"], 137)
        # Without a node universe the loaded graph is counted by kind.
        self.graph.receipt = {}
        self.service.view_cache.clear()
        totals = self.service.snapshot("Gordon", 20)["totals"]
        self.assertEqual(totals["nodes"], sum(n["kind"] == "person" for n in self.graph.nodes.values()))
        self.assertEqual(totals["edges"], 0)

    def test_people_only_notice_reaches_the_client(self):
        snapshot = self.service.snapshot("Gordon", 20)
        self.assertTrue(snapshot["people_only_notice"].startswith("Only people are displayed"))
        self.assertIn("Only people are displayed", snapshot["notice"])
        self.assertIn("Exploratory introduction routes", snapshot["notice"])
        self.assertNotIn("IN PROGRESS", snapshot["notice"])

    def test_person_refuses_institution_ids_the_way_search_does(self):
        self.graph.nodes["organization:test"] = {"id": "organization:test", "kind": "organization", "label": "Gordon Institute", "member_ids": []}
        self.graph.nodes["activity:lda:activity:1:0"] = {"id": "activity:lda:activity:1:0", "kind": "organization",
            "label": "Filing activity", "evidence_kind": "lobbying_activity"}
        self.graph.nodes["policy:test"] = {"id": "policy:test", "kind": "policy_area", "label": "Abortion"}
        self.graph.reindex()
        self.service.profile_nodes["politico-affiliation:x"] = {"id": "politico-affiliation:x", "kind": "container", "label": "Some Committee"}
        refused = {"ready": True, "status": "unknown_person", "node": None, "coworkers": []}
        for ident in ("member:A000370", "organization:test", "activity:lda:activity:1:0", "policy:test", "politico-affiliation:x"):
            self.assertEqual(self.service.person(ident), refused, ident)
            self.assertEqual(self.service.search(ident)["nodes"], [], ident)
        self.assertEqual(self.service.person("person:gordon")["node"]["id"], "person:gordon")

    def test_unknown_person_route_explains_itself_instead_of_the_heuristic(self):
        for origin in ("missing", "member:A000370"):
            route = self.service.route(origin, "person:gordon")
            self.assertEqual(route["status"], "unknown_person")
            self.assertIn("route endpoints", route["explanation"])
            self.assertNotIn("zero heuristic", route["explanation"])
        balanced = self.service.route("person:gordon", "person:stephanie")
        self.assertEqual(balanced['objective'], 'overlap_balanced')
        self.assertIn("Balances shared-background scores", balanced['explanation'])
        self.assertIn("not measurements of familiarity", balanced['explanation'])
        self.assertIn("shared-background scores", self.service.route("person:gordon", "person:stephanie", {'objective':'overlap_strength'})["explanation"])

    def test_freshness_age_and_stale_flag_are_evaluated_at_request_time(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        self.service.profiles.metadata = {"coverage": {"verified": 0}, "errors": [],
            "freshness": {"source_updated_at": None, "verified_at": old, "age_seconds": 40.4, "poll_seconds": 30, "stale": False}}
        self.service._refresh_profiles()
        self.assertEqual(self.service.metadata["freshness"]["age_seconds"], 40.4)  # the stored poll-time value is untouched
        for payload in (self.service.snapshot("Gordon", 20), self.service.overview_status(),
                        json.loads(gzip.decompress(self.service.encoded_snapshot("Gordon", 20)[0]))):
            self.assertTrue(payload["freshness"]["stale"])
            self.assertGreaterEqual(payload["freshness"]["age_seconds"], 399)
            self.assertEqual(payload["freshness"]["verified_at"], old)
        # The served bytes follow the flag when it flips on the clock, without a new poll.
        self.service.profiles.metadata["freshness"].update(verified_at=datetime.now(timezone.utc).isoformat(), stale=True, age_seconds=900.0)
        self.service._refresh_profiles()
        fresh = self.service.encoded_snapshot("Gordon", 20)
        self.assertFalse(json.loads(gzip.decompress(fresh[0]))["freshness"]["stale"])
        with patch("fec.surfaces.staffer_live_server.time") as clock:
            clock.time.return_value = time.time() + 400
            flipped = self.service.encoded_snapshot("Gordon", 20)
        self.assertNotEqual(fresh[1], flipped[1])
        self.assertTrue(json.loads(gzip.decompress(flipped[0]))["freshness"]["stale"])

    def test_effective_node_limit_shares_one_projection_and_the_revision_names_the_content(self):
        from fec.surfaces.staffer_people_graph import NODE_LIMIT
        wide = self.service.snapshot("Gordon", 1800)
        narrow = self.service.snapshot("Gordon", NODE_LIMIT)
        self.assertEqual(wide["revision"], narrow["revision"])
        self.assertEqual(wide["bounds"]["node_limit"], NODE_LIMIT)
        self.assertEqual(len(self.service.view_cache), 1)

        def projection(*args, **kwargs):
            return {"nodes": [{"id": i, "kind": "person"} for i in projection.ids], "edges": [], "bounds": {},
                    "notice": "Only people are displayed."}
        with patch("fec.surfaces.staffer_live_service.project_people", projection):
            projection.ids = ["person:gordon"]
            self.service.view_cache.clear()
            partial = self.service.snapshot("Gordon", 20)["revision"]
            projection.ids = ["person:gordon", "person:stephanie"]
            self.service.view_cache.clear()
            complete = self.service.snapshot("Gordon", 20)["revision"]
        self.assertNotEqual(partial, complete)

    def test_a_clock_cut_projection_is_not_pinned_in_any_cache(self):
        def projection(*args, **kwargs):
            projection.calls += 1
            cut = projection.calls == 1
            people = [{"id": "person:gordon", "kind": "person"}] + ([] if cut else [{"id": "person:stephanie", "kind": "person"}])
            return {"nodes": people, "edges": [], "notice": "Only people are displayed.",
                    "bounds": {"truncated": cut, "time_limited": cut, "neighborhoods_complete": not cut}}
        projection.calls = 0
        with patch("fec.surfaces.staffer_live_service.project_people", projection):
            first = self.service.encoded_snapshot("Gordon", 20)
            self.assertEqual(len(self.service.view_cache), 0)
            second = self.service.encoded_snapshot("Gordon", 20)
            third = self.service.encoded_snapshot("Gordon", 20)
        self.assertIsNot(first, second)
        self.assertNotEqual(first[1], second[1])
        self.assertIs(second, third)
        self.assertEqual(projection.calls, 2)
        self.assertTrue(json.loads(gzip.decompress(first[0]))["bounds"]["truncated"])
        self.assertFalse(json.loads(gzip.decompress(second[0]))["bounds"]["truncated"])

    def test_person_lda_node_id_form_resolves_like_the_bare_source_id(self):
        self.service.activity_provider = SimpleNamespace(to_node={"lda:lobbyist:147664": "person:gordon"})
        self.assertEqual(self.service._resolve_id("lda:lobbyist:147664"), "person:gordon")
        self.assertEqual(self.service._resolve_id("person:lda:lobbyist:147664"), "person:gordon")
        self.assertEqual(self.service.person("person:lda:lobbyist:147664")["node"]["id"], "person:gordon")
        self.assertEqual(self.service.route("person:lda:lobbyist:147664", "person:stephanie")["path"], ["person:gordon", "person:stephanie"])
        self.assertEqual(self.service._resolve_id("person:lda:lobbyist:999"), "person:lda:lobbyist:999")

    def test_existing_lda_person_node_is_available_to_ecosystem_routes(self):
        ident = "person:lda:lobbyist:154321"
        node = {"id": ident, "kind": "person", "label": "Kathleen Coulombe"}
        self.service.static.nodes[ident] = node
        self.service.router.nodes[ident] = node

        result = self.service.staffer_person_link(ident)
        self.assertEqual(result["person_id"], ident)
        self.assertTrue(result["route_available"])
        self.assertEqual(result["status"], "matched_source_identity")

    def test_reconciliation_loop_reports_a_heartbeat(self):
        service = self.service

        class Web:
            def revision(self):
                return "r1"

            def process_one(self):
                service._stop.set()
                return False

            def status(self):
                return {"status": "checking_references", "queued": 1}
        service.identity_web = Web()
        service.run_identity_reconciliation_loop()
        progress = service.snapshot("Gordon", 20)["identity_resolution"]
        self.assertEqual(progress["status"], "checking_references")
        self.assertTrue(progress["loop_heartbeat_at"].startswith("20"))

    def test_profile_progress_counts_pass_membership_and_names_what_that_means(self):
        service = self.service
        service.automatic_resolution = {"identity_groups": [
            {"primary_id": "person:hill:person:a", "alias_ids": ["politico:a"]},
            {"primary_id": "person:hill:person:b", "alias_ids": ["person:lda:lobbyist:1"]}]}
        captured = {"digests": {"politico:a": "d1", "politico:b": "d2"}, "revision": "rev-2", "receipt_sha256": "r", "prefix_sha256": "p"}
        service._identity_profile_revision, service._identity_profile_digests = "rev-1", {"politico:a": "d1"}
        service._update_profile_progress(captured)
        progress = service.identity_progress["politico_ingestion"]
        self.assertEqual((progress["status"], progress["validated_profiles"], progress["identity_checked_profiles"], progress["pending_profiles"]),
                         ("identity_check_pending", 2, 1, 1))
        self.assertEqual((progress["accepted_revision"], progress["identity_applied_revision"]), ("rev-2", "rev-1"))
        self.assertEqual(progress["politico_links_found"], 1)
        self.assertIn("not a per-profile match outcome", progress["identity_checked_semantics"])
        service._identity_profile_revision, service._identity_profile_digests = "rev-2", dict(captured["digests"])
        service._update_profile_progress(captured)
        progress = service.identity_progress["politico_ingestion"]
        self.assertEqual((progress["status"], progress["identity_checked_profiles"], progress["pending_profiles"]),
                         ("identity_checks_current", 2, 0))
        self.assertEqual(service.inventory["sourcecoverage"]["politico"]["identity_checked_profiles"], 2)


if __name__ == "__main__":
    unittest.main()
