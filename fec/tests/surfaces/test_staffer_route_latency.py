"""Staffer people-network latency: router cache keys, labels and deployment list.

Service-level tests run the real LiveService on the small fixture graph; the router,
its work-time route cache and the refresh path are the production ones.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from fec.surfaces import install_staffer_live
from fec.surfaces.staffer_live_service import LiveService, resolve_id, routing_signature
from fec.surfaces.test_staffer_live_service import ProfilesFixture, fixture_graph
from fec.tests.surfaces.test_staffer_work_time_routes import episode

FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent


def routed_service():
    """A ready service whose router can answer work_time routes between the three fixture people."""
    service = LiveService('fixture', fixture_graph())
    service.profiles = ProfilesFixture()
    service.evidence = {'episodes': [episode('person:gordon', 'office:test', '2020-01-01', '2021-01-01'),
                                     episode('person:stephanie', 'office:test', '2020-01-01', '2021-01-01'),
                                     episode('person:other-gordon', 'office:test', '2020-06-01', '2020-09-01')]}
    service._refresh_profiles(force=True)
    service.ready, service.stage = True, 'Ready'
    return service


def work_route(service, probe, origin='person:gordon', target='person:other-gordon'):
    # `limit` is part of the request (so the route-level result cache cannot answer a repeat) but not of any
    # cost input, so the hop-row cache is what a repeated request exercises.
    result = service.route(origin, target, {'objective': 'work_time', 'limit': 100 + probe})
    assert not result.get('cache_hit'), 'the request must reach the search'
    assert result['status'] == 'found', result['status']
    return result


# ------------------------------------------------------------------ 1a: nodes_revision

def test_routing_signature_covers_exactly_the_fields_hop_eligibility_reads():
    nodes = {'a': {'id': 'a', 'kind': 'person', 'label': 'A', 'name_status': 'SOURCE_NAME', 'provenance': {'x': 1}},
             'b': {'id': 'b', 'node_kind': 'person', 'label': 'B'}}
    base = routing_signature(nodes)
    assert routing_signature({key: dict(value) for key, value in nodes.items()}) == base
    for ident, field, value in [('a', 'label', 'A2'), ('a', 'kind', 'organization'), ('a', 'name_status', 'UNRESOLVED_SOURCE_NAME'),
                                ('b', 'node_kind', 'organization'), ('b', 'kind', 'person'), ('b', 'name_status', 'X')]:
        changed = {key: dict(node) for key, node in nodes.items()}
        changed[ident][field] = value
        assert routing_signature(changed) != base, (ident, field)
    assert routing_signature({**nodes, 'c': {'id': 'c', 'kind': 'person', 'label': 'C'}}) != base
    assert routing_signature({'a': nodes['a']}) != base
    unrelated = {key: dict(node) for key, node in nodes.items()}
    unrelated['a'].update(provenance={'x': 2}, source_summary='new', contacts=[{'kind': 'email'}], aliases=['Al'], is_search_match=True)
    assert routing_signature(unrelated) == base, 'fields routing never reads must not empty the caches'


def test_an_equal_content_poll_keeps_the_route_caches_warm():
    service = routed_service()
    router = service.router
    cold = work_route(service, 1)
    assert cold['neighbor_checks'] > 0
    assert work_route(service, 2)['neighbor_checks'] == 0, 'the hop rows are cached'
    revision, caches, nodes = router.nodes_revision, dict(router.closeness_routes.row_caches), router.nodes
    service._refresh_profiles()  # every poll rebuilds router.nodes as a new, equal mapping
    assert service.router is router
    assert router.nodes is not nodes and router.nodes == nodes, 'the poll did install a fresh, equal mapping'
    assert router.nodes_revision == revision
    assert dict(router.closeness_routes.row_caches) == caches
    assert work_route(service, 3)['neighbor_checks'] == 0, 'a poll with unchanged content must not empty the row cache'


def test_a_label_change_bumps_nodes_revision_and_empties_the_row_cache():
    service = routed_service()
    service.profiles.nodes = [{'id': 'profile:x', 'kind': 'person', 'label': 'Xavier One', 'member_ids': [],
                               'committee_ids': [], 'policy_ids': []}]
    service.profiles.accepted = [{'profile_uuid': 'x'}]
    service._refresh_profiles()           # new profile content: a new router
    router = service.router
    assert router.nodes_revision == 0
    work_route(service, 1)
    assert work_route(service, 2)['neighbor_checks'] == 0
    service.profiles.nodes[0]['label'] = 'Xavier Two'   # same accepted rows, so the same profile revision
    service._refresh_profiles()
    assert service.router is router, 'only the nodes changed; the route index is kept'
    assert router.nodes_revision == 1
    assert router.nodes['profile:x']['label'] == 'Xavier Two'
    assert work_route(service, 3)['neighbor_checks'] > 0, 'the routing fields changed, so the rows are recomputed'
    assert work_route(service, 4)['neighbor_checks'] == 0
    service.profiles.nodes[0]['source_summary'] = 'a field routing never reads'
    service._refresh_profiles()
    assert router.nodes_revision == 1
    assert work_route(service, 5)['neighbor_checks'] == 0


def test_a_rebuilt_router_starts_a_fresh_revision_and_signature():
    service = routed_service()
    first = service.router
    service._refresh_profiles(force=True)
    assert service.router is not first
    assert service.router.nodes_revision == 0
    assert service.router.routing_signature == routing_signature(service.router.nodes)


def test_the_service_and_the_published_view_resolve_ids_by_the_same_rules():
    """LiveService._resolve_id and the pure resolve_id the lock-free readers use are one rule written twice."""
    service = routed_service()
    service.identity_aliases = {'person:b': 'person:a', 'person:a': 'person:c', 'loop:x': 'loop:y', 'loop:y': 'loop:x'}
    service.activity_provider = SimpleNamespace(to_node={'lda:lobbyist:1': 'person:gordon', 'person:lda:lobbyist:2': 'person:stephanie'})
    service.static.nodes['person:lda:lobbyist:3'] = {'id': 'person:lda:lobbyist:3', 'kind': 'person', 'label': 'Present Node'}
    for value in ['person:gordon', '  person:gordon  ', 'lda:lobbyist:1', 'lda:lobbyist:9', 'person:lda:lobbyist:1', 'lda:lobbyist:2',
                  'person:lda:lobbyist:2', 'person:lda:lobbyist:3', 'person:lda:lobbyist:9', 'person:b', 'person:a', 'loop:x', 'loop:y',
                  '', 'unknown', 'Mixed:Case', 42, None]:
        expected = resolve_id(value, service.static.nodes, service.identity_aliases, service.activity_provider)
        assert service._resolve_id(value) == expected, value
    assert service._resolve_id('person:b') == 'person:c' and service._resolve_id('lda:lobbyist:1') == 'person:gordon'
    assert service._resolve_id('person:lda:lobbyist:3') == 'person:lda:lobbyist:3', 'a node id that exists is left alone'
    no_provider = SimpleNamespace(nodes={})
    assert resolve_id('lda:lobbyist:7', no_provider.nodes, {}, None) == 'person:lda:lobbyist:7'


# ------------------------------------------------------------------ 1c: labels

def test_the_snapshot_names_the_search_that_runs():
    service = routed_service()
    routing = service.snapshot('Gordon', 20)['routing']
    assert routing['algorithm'] == 'Bidirectional Dijkstra'
    assert routing['heuristic'] == 0
    assert routing['algorithm'] == service.router._base()['algorithm'], 'one label for the one search'
    assert 'A*' not in routing['algorithm']


@pytest.mark.parametrize('relative', ['index.html', 'README.md'])
def test_the_page_and_readme_call_the_staffer_search_bidirectional_dijkstra(relative):
    text = (FEC / 'surfaces' / 'staffer_live' / relative).read_text(encoding='utf-8')
    assert 'bidirectional Dijkstra search' in text
    assert 'uses Dijkstra search' not in text


# ------------------------------------------------------------------ deployment list

def test_every_module_the_live_view_imports_is_in_the_installer_list():
    """A module the service imports but the installer does not copy would break routes in the live tree."""
    listed = set(install_staffer_live.FILES)
    packages = {('fec', 'surfaces'), ('fec', 'staffer_network')}

    def imports_of(name):
        path = ROOT / name
        package = path.relative_to(ROOT).with_suffix('').parts[:-1]
        found = set()
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ImportFrom):
                base = list(package[:len(package) - (node.level - 1)]) if node.level else []
                full = base + (node.module.split('.') if node.module else [])
                if tuple(full[:2]) in packages:
                    found.update(['/'.join(full) + '.py'] if len(full) > 2 else ['/'.join(full + [a.name]) + '.py' for a in node.names])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split('.')
                    if tuple(parts[:2]) in packages and len(parts) > 2:
                        found.add('/'.join(parts) + '.py')
        return {ref for ref in found if (ROOT / ref).is_file()}

    queue = [name for name in listed if name.endswith('.py') and not Path(name).name.startswith('test_')]
    seen, missing = set(), {}
    while queue:
        name = queue.pop()
        if name in seen or not (ROOT / name).is_file():
            continue
        seen.add(name)
        for ref in imports_of(name):
            if ref not in listed:
                missing.setdefault(ref, []).append(name)
            else:
                queue.append(ref)
    assert not missing, 'imported but not installed: ' + repr({ref: sorted(users)[:2] for ref, users in missing.items()})
    assert 'fec/surfaces/staffer_route_cancel.py' in listed
    assert sum(name == 'fec/surfaces/staffer_route_cancel.py' for name in install_staffer_live.FILES) == 1