"""contact_group answers from a key index built once per nodes mapping, and answers exactly as the scan it replaced did.

The scan read every node on every call (the service calls it twice per route iteration, about 0.2 s each on the live
nodes). The index holds node ids by contact key, is kept on the router, and is built again when the nodes mapping is
replaced, its length changes or router.nodes_revision moves. A differential test pins every answer to the old scan.
"""
from concurrent.futures import ThreadPoolExecutor
import gc
import random
from types import SimpleNamespace
import weakref

import pytest

from fec.surfaces import staffer_distinct_routes as distinct
from fec.surfaces.staffer_distinct_routes import contact_group, contact_key
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.tests.surfaces.test_staffer_route_latency import routed_service

NAMES = ['Ana Pérez', 'ANA PEREZ', 'ana  perez', 'Ana-Perez', "Sean O'Brien", 'sean o brien', 'Jean-Luc Picard', 'JEAN LUC PICARD',
         'Zoë Washburne', 'Zoe Washburne', 'Mark Smith', 'Mark  Smith', 'Mark Smith Jr', 'Li Wei', 'LI  WEI', '  ', '!!', 'ＡＢＣ Ｄｅｆ', 'ABC Def']


def old_contact_group(router, people):
    """contact_group as it was: score every node on every call."""
    keys = {contact_key(router.nodes.get(p, {}), p) for p in people}
    return {p for p, n in router.nodes.items() if contact_key(n, p) in keys}


def random_nodes(rng, count=140):
    nodes = {}
    for i in range(count):
        node = {'kind': 'person'}
        draw = rng.random()
        if draw < .62:
            node['label'] = rng.choice(NAMES)
        elif draw < .7:
            node['label'] = ''                      # falls through to name
            node['name'] = rng.choice(NAMES)
        elif draw < .78:
            node['name'] = rng.choice(NAMES)
        elif draw < .85:
            node['label'] = None                    # no name at all: the id is the key
        # the rest carry no label or name field
        nodes['n%03d' % i] = node
    return nodes


def asked(rng, nodes):
    ids = sorted(nodes)
    pool = ids + ['unknown-%d' % k for k in range(5)]     # ids that are not nodes
    return [set(rng.sample(pool, rng.randint(0, 6))) for _ in range(6)] + [set(), set(ids), {ids[0]}]


def test_every_answer_equals_the_old_scan_on_random_nodes_and_people():
    asked_for = answered = 0
    for seed in range(40):
        rng = random.Random(seed)
        router = SimpleNamespace(nodes=random_nodes(rng))
        for people in asked(rng, router.nodes):
            got = contact_group(router, people)
            assert got == old_contact_group(router, people), (seed, people)
            assert isinstance(got, set)
            asked_for += 1
            answered += len(got) > len(people & router.nodes.keys())
    assert asked_for >= 360 and answered > 40, 'some groups hold more than the people asked about'


def test_the_index_is_built_once_per_nodes_mapping(monkeypatch):
    calls = []
    real = distinct.contact_key
    monkeypatch.setattr(distinct, 'contact_key', lambda node, fallback: calls.append(fallback) or real(node, fallback))
    router = SimpleNamespace(nodes=random_nodes(random.Random(1)))
    contact_group(router, {'n001', 'n002'})
    assert len(calls) == len(router.nodes) + 2, 'one pass over the nodes, and one key per person asked about'
    calls.clear()
    for _ in range(5):
        contact_group(router, {'n001', 'n002', 'n003'})
    assert len(calls) == 15, 'later calls score only the people asked about'


def test_a_replaced_mapping_a_new_length_or_a_new_nodes_revision_builds_again_and_nothing_else_does():
    rng = random.Random(3)
    nodes = random_nodes(rng)
    router = SimpleNamespace(nodes=nodes, nodes_revision=0)
    people = {'n001', 'n002', 'n003', 'n004'}
    first = contact_group(router, people)
    held = router._contact_groups
    contact_group(router, people)
    assert router._contact_groups is held, 'same mapping, same length, same revision: the index is kept'
    router.nodes = dict(nodes)
    assert contact_group(router, people) == first and router._contact_groups is not held, 'an equal new mapping is indexed again'
    held = router._contact_groups
    router.nodes['added'] = {'label': 'Ana Perez'}
    assert contact_group(router, {'added', 'n001'}) == old_contact_group(router, {'added', 'n001'}) and router._contact_groups is not held
    held = router._contact_groups
    router.nodes['n001'] = {'label': 'Somebody Else'}          # same length: only the revision announces the change
    router.nodes_revision = 1
    assert contact_group(router, {'n001'}) == old_contact_group(router, {'n001'}) == {'n001'}
    assert router._contact_groups is not held


def test_a_router_without_a_nodes_revision_is_keyed_by_the_mapping_and_its_length():
    router = SimpleNamespace(nodes={'a': {'label': 'Same Name'}, 'b': {'label': 'Same Name'}})
    assert contact_group(router, {'a'}) == {'a', 'b'}
    router.nodes = {'a': {'label': 'Same Name'}, 'b': {'label': 'Other'}}
    assert contact_group(router, {'a'}) == {'a'}


def test_concurrent_first_calls_build_the_index_once_and_agree(monkeypatch):
    calls = []
    real = distinct.contact_key
    monkeypatch.setattr(distinct, 'contact_key', lambda node, fallback: calls.append(1) or real(node, fallback))
    router = SimpleNamespace(nodes=random_nodes(random.Random(2), count=600))
    people = [{'n%03d' % k, 'n%03d' % (k + 1)} for k in range(8)]
    with ThreadPoolExecutor(8) as pool:
        answers = list(pool.map(lambda group: contact_group(router, group), people))
    assert len(calls) == len(router.nodes) + 16
    assert answers == [old_contact_group(router, group) for group in people]


def test_a_wrong_index_is_caught_by_the_differential_check(monkeypatch):
    """Negative control: an index that does not fold case and spacing the way contact_key does disagrees with the scan."""
    rng = random.Random(4)
    router = SimpleNamespace(nodes=random_nodes(rng))
    people = {p for p in router.nodes if router.nodes[p].get('label') in ('Ana Pérez', 'ANA PEREZ', 'ana  perez')}
    assert people and contact_group(router, people) == old_contact_group(router, people)
    router._contact_groups = (router.nodes, (len(router.nodes), None), {})        # an empty index
    assert contact_group(router, people) != old_contact_group(router, people)
    router._contact_groups = None
    wrong = {}
    for ident, node in router.nodes.items():
        wrong.setdefault(str(node.get('label')), []).append(ident)                  # keyed by the raw label
    router._contact_groups = (router.nodes, (len(router.nodes), None), wrong)
    assert any(contact_group(router, {p}) != old_contact_group(router, {p}) for p in router.nodes)


# --------------------------------------------------------------------------- through the real router and service
def test_the_service_keeps_the_index_across_equal_polls_and_follows_a_label_change():
    service = routed_service()
    router = service.router
    people = {'person:gordon'}
    assert contact_group(router, people) == old_contact_group(router, people) == {'person:gordon', 'person:other-gordon'}
    service._refresh_profiles()          # an equal poll: the same router, the same answers
    assert service.router is router
    assert contact_group(router, people) == old_contact_group(router, people)
    service.profiles.nodes = [{'id': 'profile:x', 'kind': 'person', 'label': 'Gordon Holzberg', 'member_ids': [],
                               'committee_ids': [], 'policy_ids': []}]
    service.profiles.accepted = [{'profile_uuid': 'x'}]
    service._refresh_profiles()          # a new profile with the same displayed name: a new router
    router = service.router
    assert contact_group(router, people) == old_contact_group(router, people)
    assert 'profile:x' in contact_group(router, people)
    service.profiles.nodes[0]['label'] = 'Somebody Else'
    service._refresh_profiles()          # the label differs: the service bumps nodes_revision, and the index follows
    assert service.router is router and router.nodes_revision == 1
    assert contact_group(router, people) == old_contact_group(router, people)
    assert 'profile:x' not in contact_group(router, people)


@pytest.mark.parametrize('objective', ['work_time', 'evidence_cost'])
def test_avoiding_a_person_removes_every_record_with_the_same_displayed_name(objective):
    router = routed_service().router
    with pytest.raises(ValueError, match='endpoint'):     # the origin shares a displayed name with the person avoided
        router.route_many('person:gordon', ['person:stephanie'], {'objective': objective, 'avoid_people': ('person:other-gordon',)})
    answer = router.route_many('person:gordon', ['person:other-gordon'], {'objective': objective, 'avoid_people': ('person:stephanie',)})
    assert answer['status'] == 'found' and 'person:stephanie' not in answer['node_ids']


# --------------------------------------------------------------------------- a replaced nodes mapping is not kept alive
class Watched(dict):
    """A nodes mapping that can be weakly referenced (a plain dict cannot be)."""


def a_router_whose_nodes_mapping_the_service_replaces():
    """(router, weak reference to the mapping its name index was built from) after the service installed a new mapping on it.

    A label change under an unchanged accepted snapshot is the case: the router is kept, its nodes mapping is replaced and
    nodes_revision moves (test_staffer_route_latency.py).
    """
    service = routed_service()
    service.profiles.nodes = [{'id': 'profile:x', 'kind': 'person', 'label': 'Xavier One', 'member_ids': [],
                               'committee_ids': [], 'policy_ids': []}]
    service.profiles.accepted = [{'profile_uuid': 'x'}]
    service._refresh_profiles()          # new profile content: a new router
    router = service.router
    router.nodes = Watched(router.nodes)           # the mapping the router holds now, which can be watched
    contact_group(router, {'person:gordon'})
    assert router._contact_groups[0] is router.nodes, 'the name index was built from it'
    reference = weakref.ref(router.nodes)
    service.profiles.nodes[0]['label'] = 'Xavier Two'    # the same accepted rows: a new mapping is installed on the same router
    service._refresh_profiles()
    assert service.router is router and router.nodes_revision == 1 and not isinstance(router.nodes, Watched)
    return router, reference


def test_installing_a_new_nodes_mapping_drops_the_name_index_so_the_replaced_mapping_is_freed():
    router, reference = a_router_whose_nodes_mapping_the_service_replaces()
    assert router._contact_groups is None and reference() is None, 'nothing holds the replaced mapping any more'
    people = {'person:gordon'}
    assert contact_group(router, people) == old_contact_group(router, people)
    assert router._contact_groups[0] is router.nodes, 'the next request builds the index again, from the new mapping'


def test_without_the_drop_the_name_index_holds_the_replaced_mapping_for_as_long_as_the_router_lives(monkeypatch):
    """Negative control: the router as it was before the change, which kept the index across the install."""
    monkeypatch.setattr(AdvocacyRouter, 'forget_contact_groups', lambda self: None)
    router, reference = a_router_whose_nodes_mapping_the_service_replaces()
    assert reference() is not None and router._contact_groups[0] is reference()
    gc.collect()
    assert reference() is not None, 'a collection does not free it: a live router holds it'


def test_forgetting_the_name_index_changes_no_answer():
    router = routed_service().router
    people = {'person:gordon'}
    first = contact_group(router, people)
    held = router._contact_groups
    router.forget_contact_groups()
    assert router._contact_groups is None
    assert contact_group(router, people) == first == old_contact_group(router, people)
    assert router._contact_groups is not held and router._contact_groups[0] is router.nodes
