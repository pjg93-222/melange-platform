import gzip
import json
from pathlib import Path
import tempfile
import unittest

from fec.surfaces.staffer_activity_index import ActivityIndex, build_index, sha
from fec.surfaces.staffer_routing import AdvocacyRouter


class ActivityIndexTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        activities = [{"source_activity_id": "lda:activity:" + ident, "filing_year": 2026,
                       "filing_period": "second_quarter", "dt_posted": "2026-07-17", "filing_uuid": ident,
                       "registrant_source_org_id": "lda:registrant:" + ident, "client_source_org_id": "lda:client:" + ident, "general_issue_code": "BAN"} for ident in ("a", "b")]
        edges = [{"edge_id": "edge:" + person + activity, "subject_id": "lda:lobbyist:" + person,
                  "object_id": "lda:activity:" + activity, "predicate": "NAMED_ON_ACTIVITY"}
                 for person, activity in (("1", "a"), ("2", "a"), ("2", "b"), ("3", "b"))]
        specs = []
        for role, records in (("activity_assertions", activities), ("industry_edges", edges)):
            path = self.root / (role + ".ndjson.gz")
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                for row in records:
                    stream.write(json.dumps(row) + "\n")
            specs.append({"role": role, "path": path.name, "bytes": path.stat().st_size, "sha256": sha(path), "records": len(records)})
        (self.root / "lda_source_native_corpus_receipt.json").write_text(json.dumps({"status": "PASS", "files": specs}), encoding="utf-8")
        self.database = self.root / "activities.sqlite"
        self.receipt = build_index(self.root, self.database)
        self.nodes = {"person:lda:lobbyist:" + ident: {"id": "person:lda:lobbyist:" + ident,
                      "kind": "person", "label": "Person " + ident, "lda_source_person_id": "lda:lobbyist:" + ident}
                      for ident in ("1", "2", "3")}

    def router(self, nodes=None):
        nodes = nodes or self.nodes
        return AdvocacyRouter(nodes, [], "fixture", "2026-09-04", ActivityIndex(self.database, nodes))

    def test_complete_activity_pages_preserve_each_named_record(self):
        index = ActivityIndex(self.database, self.nodes)
        first = index.records('person:lda:lobbyist:2', 0, 1)
        second = index.records('person:lda:lobbyist:2', 1, 1)
        self.assertEqual(first['total'], 2)
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        self.assertNotEqual(first['records'][0]['source_activity_id'], second['records'][0]['source_activity_id'])
        self.assertEqual(index.records('absent')['total'], 0)

    def test_complete_sparse_index_supports_general_two_hop_route(self):
        self.assertEqual(self.receipt["counts"]["named_on_activity"], 4)
        router = self.router()
        result = router.route("person:lda:lobbyist:1", "person:lda:lobbyist:3")
        self.assertEqual(result["cost"], 4)
        self.assertEqual(len(result["hops"]), 2)
        self.assertEqual(result["hops"][0]["relationship"], "co_reported_lobbying_activity")
        self.assertEqual(router.coworkers("person:lda:lobbyist:1")["coworkers"], [])
        self.assertEqual(router.reported_collaborators("person:lda:lobbyist:1")["total"], 1)

    def test_unique_spine_join_and_ambiguous_spines_remain_separate(self):
        nodes = dict(self.nodes)
        nodes["person:hill:person:unique"] = {"id": "person:hill:person:unique", "kind": "person", "label": "Unique spine", "lda_source_person_id": "lda:lobbyist:1"}
        for suffix in ("a", "b"):
            nodes["person:hill:person:" + suffix] = {"id": "person:hill:person:" + suffix, "kind": "person", "label": "Ambiguous spine " + suffix, "lda_source_person_id": "lda:lobbyist:2"}
        index = ActivityIndex(self.database, nodes)
        self.assertEqual(index.to_node["lda:lobbyist:1"], "person:hill:person:unique")
        self.assertEqual(index.to_node["lda:lobbyist:2"], "person:lda:lobbyist:2")
        self.assertNotIn("person:hill:person:a", index.to_source)

    def test_explicit_user_identity_union_uses_all_ids_and_emits_basis(self):
        nodes = dict(self.nodes)
        del nodes["person:lda:lobbyist:2"]
        nodes["person:lda:lobbyist:1"] = {**nodes["person:lda:lobbyist:1"], "source_person_ids": ["lda:lobbyist:1", "lda:lobbyist:2"], "identity_status": "USER_CONFIRMED_SAME_PERSON"}
        result = self.router(nodes).route("person:lda:lobbyist:1", "person:lda:lobbyist:3")
        self.assertEqual(result["cost"], 2)
        self.assertEqual(result["hops"][0]["provenance"]["identity_overlays"][0]["basis"], "USER_CONFIRMED_SAME_PERSON")
        self.assertEqual(result["hops"][0]["provenance"]["representative_period"]["source_person_ids"], ["lda:lobbyist:2", "lda:lobbyist:3"])

    def test_review_filter_limits_and_checksum_failure(self):
        router = self.router()
        self.assertEqual(router.route("person:lda:lobbyist:1", "person:lda:lobbyist:3", {"include_review": False})["status"], "unreachable")
        self.assertEqual(router.route("person:lda:lobbyist:1", "person:lda:lobbyist:3", {"max_neighbor_checks": 1})["status"], "found")
        with self.database.open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(ValueError, "checksum"):
            ActivityIndex(self.database, self.nodes)


if __name__ == "__main__":
    unittest.main()
