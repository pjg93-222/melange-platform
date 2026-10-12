"""Compare ranked alternatives with exhaustive simple paths on small graphs."""
import math
import random
import threading

import pytest

from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.staffer_overlap_routes import DISTANCE_SCALE, BALANCED_STEP_UNITS, work_time_strength
from fec.surfaces.staffer_live_service import LiveService
from fec.surfaces.staffer_live_server import Handler
from fec.tests.surfaces.test_staffer_work_time_routes import person, episode


@pytest.mark.parametrize('objective', ['evidence_cost', 'fewest_steps', 'work_time',
                                      'overlap_balanced', 'overlap_strength', 'overlap_fewest_steps'])
@pytest.mark.parametrize('office', [False, True])
def test_alternatives_match_exhaustive_paths(objective, office):
    for seed in range(8):
        rng = random.Random(seed)
        nodes = {p: person(p.upper()) for p in 'abcdef'}
        episodes, adjacency = [], {p: {} for p in nodes}
        for a in nodes:
            for b in nodes:
                if a >= b or rng.random() > .5:
                    continue
                end = rng.choice(['2020-02-01', '2020-07-01', '2021-01-01'])
                days = {'2020-02-01': 31, '2020-07-01': 182, '2021-01-01': 366}[end]
                episodes.extend(episode(p, 'office:' + a + b, '2020-01-01', end) for p in (a, b))
                adjacency[a][b] = adjacency[b][a] = days
        router = AdvocacyRouter(nodes, episodes, 'fixture', '2026-09-08')
        targets = ['e', 'f'] if office else ['f']
        paths = []
        def visit(path):
            if path[-1] in targets:
                paths.append(path)
                if objective != 'work_time':
                    return
            for p in adjacency[path[-1]]:
                if p not in path:
                    visit(path + [p])
        visit(['a'])
        def rank(path):
            if objective == 'work_time':
                index = router.closeness_routes.index(router._options({'objective': 'work_time'}))
                return (sum(-math.log(.8 * index.pair(a,b)['strength']) for a, b in zip(path, path[1:])), len(path)-1)
            # Each other model gives these symmetric, historical fixture edges
            # equal weight, so exhaustive hop counts determine the ranking.
            return (len(path)-1,)
        options = {'objective': objective, 'route_count': 5, 'alternative_strategy': 'ranked', 'uncertainty_weight': 0}
        result = router.route_many('a', targets, options)
        routes = result['routes']
        assert len(routes) == min(5, len(paths))
        assert len({tuple(r['node_ids']) for r in routes}) == len(routes)
        actual, expected = [rank(r['node_ids']) for r in routes], sorted(map(rank, paths))[:5]
        if objective == 'work_time':
            assert [r[0] for r in actual] == pytest.approx([r[0] for r in expected])
        else:
            assert actual == expected
        for index, route in enumerate(routes):
            assert route['route_rank'] == index + 1
            assert len(set(route['node_ids'])) == len(route['node_ids'])
            assert route['target'] in targets
            assert [(h['source'], h['target']) for h in route['hops']] == list(zip(route['node_ids'], route['node_ids'][1:]))
        # Search exclusions must not leak into subsequent or cached requests.
        single = router.route_many('a', targets, {'objective': objective, 'uncertainty_weight': 0})
        assert single['node_ids'] == result['node_ids']
        assert router.route_many('a', targets, options)['routes_returned'] == len(routes)


def test_unknown_same_person_and_invalid_count():
    router = AdvocacyRouter({'a': person('A')}, [], 'r', '2026-09-08')
    assert router.route('a', 'missing', {'route_count': 3})['routes'] == []
    result = router.route('a', 'a', {'route_count': 5})
    assert len(result['routes']) == 1
    assert result['routes'][0]['steps'] == 0
    for count in [0, 6, 1.5, 'bad', float('nan'), float('inf')]:
        with pytest.raises(ValueError):
            router.route('a', 'a', {'route_count': count})
    assert Handler.routing_options(None, {'route_count': ['3']})['route_count'] == '3'


def test_service_serializes_each_office_contact_and_evidence():
    nodes = {p: {'id': p, **person(p.upper())} for p in 'abc'}
    episodes = [episode(p, 'office:x', '2020-01-01', '2021-01-01') for p in nodes]
    router = AdvocacyRouter(nodes, episodes, 'r', '2026-09-08')
    router.office_index = {'office:x': {'id': 'office:x', 'label': 'Office X', 'records_through': '2021-01-01',
        'roster': {p: {'title': 'Title ' + p, 'basis': 'current_profile'} for p in 'bc'}}}
    service = LiveService.__new__(LiveService)
    service.lock = threading.RLock()
    service.ready = True
    service._assertion_update_pending = False
    service.router = router
    service._resolve_id = lambda value: value
    result = service.office_route('a', 'office:x', {'route_count': 3, 'objective': 'work_time', 'alternative_strategy': 'ranked'})
    assert len(result['routes']) == 3
    for route in result['routes']:
        assert route['recommended_contact']['title'] == 'Title ' + route['target']
        assert route['nodes'][-1]['id'] == route['target']
        assert route['hops'][-1]['target_label'] == route['target'].upper()
        assert route['hops'][0]['provenance']
        assert route['found']
