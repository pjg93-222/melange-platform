import itertools

import pytest

from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.staffer_distinct_routes import route_contexts
from fec.surfaces.staffer_live_server import Handler
from fec.tests.surfaces.test_staffer_work_time_routes import person, episode


def fixture(edges, labels=None):
    nodes = {p: person((labels or {}).get(p, p.upper())) for edge in edges for p in edge}
    episodes = [episode(p, 'office:'+a+b, '2020-01-01', '2021-01-01')
                for a,b in edges for p in (a,b)]
    return AdvocacyRouter(nodes, episodes, 'fixture', '2026-09-10')


@pytest.mark.parametrize('objective', ['evidence_cost','work_time','overlap_balanced','fewest_steps'])
def test_searches_beyond_shared_bottleneck_and_preserves_request_isolation(objective):
    # Many cheap detours all depend on b; the real alternative needs four hops.
    edges = [('a','b'),('b','z'),('a','x'),('x','y'),('y','w'),('w','z')]
    edges += [(a,b) for n in range(7) for a,b in [('a','n'+str(n)),('n'+str(n),'b')]]
    router = fixture(edges)
    options = {'objective': objective, 'route_count': 3}
    ranked = router.route('a','z',{**options,'alternative_strategy':'ranked'})
    if objective != 'work_time':
        assert all('b' in r['node_ids'] for r in ranked['routes'])
    result = router.route('a','z',options)
    assert result['routes_returned'] == 2
    assert any(r['node_ids'] == ['a','x','y','w','z'] for r in result['routes'])
    assert not (set(result['routes'][0]['node_ids'][1:-1]) & set(result['routes'][1]['node_ids'][1:-1]))
    assert result['portfolio_optimal'] is False
    assert result['alternatives_stop_reason'] == 'no_further_route_under_accumulated_exclusions'
    avoided = router.route('a','z',{**options,'avoid_people':('b',)})
    assert all('b' not in r['node_ids'] for r in avoided['routes'])
    assert router.route('a','z',options)['node_ids'] == result['node_ids']


@pytest.mark.parametrize('objective', ['evidence_cost','work_time'])
def test_same_workplace_detours_are_not_evidence_independent(objective):
    nodes = {p: person(p.upper()) for p in 'abcd'}
    rows = [episode(p,'office:committee','2020-01-01','2021-01-01') for p in nodes]
    router = AdvocacyRouter(nodes, rows, 'r', '2026-09-10')
    options = {'objective':objective,'route_count':3}
    distinct = router.route('a','d',options)
    assert len(distinct['routes']) == 3
    assert distinct['routes'][1]['dependence_on_previous_routes']['shared_workplace_contexts'] == 1
    independent = router.route('a','d',{**options,'alternative_strategy':'independent'})
    assert len(independent['routes']) == 1


@pytest.mark.parametrize('objective', ['evidence_cost','work_time'])
def test_independent_search_finds_separate_contexts(objective):
    router = fixture([('a','b'),('b','d'),('a','c'),('c','d')])
    result = router.route('a','d',{'objective':objective,'route_count':3,'alternative_strategy':'independent'})
    assert len(result['routes']) == 2
    assert not route_contexts(result['routes'][0]) & route_contexts(result['routes'][1])


def test_same_name_records_do_not_manufacture_distinct_contacts_or_merge_identities():
    router = fixture([('a','b'),('b','d'),('a','c'),('c','d')], {'b':'Michael P Darner','c':'Michael P Darner'})
    result = router.route('a','d',{'route_count':3})
    assert result['routes_returned'] == 1
    assert router.route('a','d',{'avoid_people':'b'})['status'] == 'unreachable'
    assert 'b' in router.nodes and 'c' in router.nodes
    assert router.route('a','d',{'route_count':3,'alternative_strategy':'ranked'})['routes_returned'] == 2


@pytest.mark.parametrize('objective',['evidence_cost','work_time'])
def test_office_has_distinct_terminal_contacts_including_zero_hop(objective):
    router = fixture([('a','b'),('a','c')])
    result = router.route_many('a',['a','b','c'],{'objective':objective,'route_count':5})
    assert len(result['routes']) == 3
    assert result['routes'][0]['node_ids'] == ['a']
    assert len({r['target'] for r in result['routes']}) == 3
    for a,b in itertools.combinations(result['routes'],2):
        assert not set(a['node_ids'][1:]) & set(b['node_ids'][1:])


def test_options_validate_and_http_retains_search_constraints():
    router = fixture([('a','b')])
    for options in [{'alternative_strategy':'anything'}, {'avoid_people':'missing'}, {'avoid_people':'a'}]:
        with pytest.raises(ValueError):
            router.route('a','b',options)
    assert Handler.routing_options(None,{'alternative_strategy':['distinct'],'avoid_people':['a,b']}) == {
        'alternative_strategy':'distinct','avoid_people':'a,b'}
    assert router.route('a','missing',{'route_count':3})['routes'] == []


def test_ranked_spurs_cannot_reintroduce_explicitly_avoided_person():
    router = fixture([('a','b'),('b','z'),('a','c'),('c','z'),('c','b'),('a','d'),('d','z')])
    result = router.route('a','z',{'route_count':5,'alternative_strategy':'ranked','avoid_people':'b'})
    assert result['routes_returned'] == 2
    assert all('b' not in r['node_ids'] for r in result['routes'])
