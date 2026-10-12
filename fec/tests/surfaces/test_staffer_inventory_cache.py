import json
from types import SimpleNamespace
import threading

from fec.surfaces import staffer_live_service as service_module
from fec.surfaces import staffer_person_inventory as inventory_module


def test_index_keeps_facts_and_contacts_from_one_generation_during_refresh(monkeypatch):
    service = service_module.LiveService.__new__(service_module.LiveService)
    service.ready = True
    service._assertion_update_pending = False
    service._explorer_index = None
    service._explorer_lock = threading.Lock()
    service.revision = 'old'
    service.static = SimpleNamespace(nodes={})
    service.profile_nodes = {}
    service.identity_aliases = {}
    old_facts, old_contacts = object(), object()
    service.router = SimpleNamespace(person_facts=old_facts)
    service.contacts = old_contacts
    service.source_catalog = service.lda_catalog = None
    service.metrics = {}
    class RefreshOnUnlock:
        def __enter__(self): return self
        def __exit__(self, *args):
            service.router = SimpleNamespace(person_facts=object())
            service.contacts = object()
            service.revision = 'new'
    service.lock = RefreshOnUnlock()
    def build(nodes, aliases, revision, catalog, lda, facts, contacts, inventory):
        assert revision == 'old' and facts is old_facts and contacts is old_contacts
        return {'revision': revision, 'gzip': b'old', 'raw': b'old', 'etag': 'old', 'payload': {'total': 0}}
    monkeypatch.setattr(service_module, 'build_index', build)
    assert service.network_index() == (b'old', 'old')
    assert service._explorer_index['revision'] != service.revision  # Next request must rebuild.


def test_cached_inventory_reloads_only_current_user_decisions(tmp_path, monkeypatch):
    a, b = "person:lda:lobbyist:1", "person:lda:lobbyist:2"
    graph = SimpleNamespace(nodes={i: {"id": i, "label": "Person Example", "kind": "person"} for i in [a, b]})
    service = service_module.LiveService(tmp_path, graph)
    service._source_nodes = dict(graph.nodes)
    service.inventory = {
        "nodes": {a: {"id": a}}, "raw_nodes": dict(graph.nodes),
        "identity_aliases": {b: a}, "identity_groups": [{"old": "overlay"}],
        "counts": {"lda_source_person_ids": 2, "user_confirmed_identity_groups": 1},
        "sourcecoverage": {"lda": {"source_ids": 2, "examples": [{"source_person_id": "lda:lobbyist:2",
            "node_id": a, "identity_merge": True, "identity_status": "USER_CONFIRMED_SAME_PERSON"}]}},
        "sources": [{"role": "verified_source", "sha256": "source_hash"},
                    {"role": "user_confirmed_identity_overlay", "sha256": "old_decision"}],
        "exceptions": ["Known source exception"],
    }
    service._cache_source_inventory()
    path = tmp_path / "decisions.json"
    monkeypatch.setattr(inventory_module, "USER_ASSERTIONS", path)
    path.write_text(json.dumps({"identity_groups": [{"primary_id": a, "alias_ids": [b],
        "source_person_ids": ["lda:lobbyist:1", "lda:lobbyist:2"], "display_name": "Confirmed Current Name",
        "status": "USER_CONFIRMED_SAME_PERSON", "asserted_at": "2026-09-04", "provenance": {"source": "user"}}]}))
    def unexpected_reload(*args):
        raise AssertionError("Sealed inventory must not reload during cached rebuild")
    monkeypatch.setattr(service_module, "load_inventory", unexpected_reload)
    first = service._inventory_for_rebuild(graph)
    assert first["identity_aliases"] == {b: a}
    assert first["nodes"][a]["label"] == "Confirmed Current Name"
    assert first["exceptions"] == ["Known source exception"]
    assert len([s for s in first["sources"] if s.get("role") == "user_confirmed_identity_overlay"]) == 1
    first["sourcecoverage"]["lda"]["source_ids"] = 999
    # Rebuilding after a changed decision starts from the raw source graph.
    path.write_text('{"identity_groups": [], "keep_separate_pairs": []}')
    second = service._inventory_for_rebuild(graph)
    assert second["identity_aliases"] == {}
    assert second["nodes"] == {}
    assert set(graph.nodes) == {a, b}
    assert second["sourcecoverage"]["lda"]["source_ids"] == 2
    assert second["sourcecoverage"]["lda"]["examples"][0]["node_id"] == b
    assert not second["sourcecoverage"]["lda"]["examples"][0]["identity_merge"]
    assert "identity_aliases" not in service._source_inventory


def test_fixture_without_inventory_cache_uses_loader(tmp_path, monkeypatch):
    graph = SimpleNamespace(nodes={})
    service = service_module.LiveService(tmp_path, graph)
    sentinel = {"source": "fixture loader"}
    monkeypatch.setattr(service_module, "load_inventory", lambda root, current: sentinel)
    assert service._inventory_for_rebuild(graph) is sentinel
