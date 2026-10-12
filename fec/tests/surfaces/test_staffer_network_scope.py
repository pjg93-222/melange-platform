from copy import deepcopy
from types import SimpleNamespace
import pytest

from fec.surfaces.staffer_network_scope import intern_title, intern_record
from fec.surfaces.staffer_connection_facts import PersonFacts
from fec.surfaces.staffer_network_explorer import build_index
from fec.surfaces.staffer_office_routes import office_index
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.staffer_overlap_routes import OverlapRoutes


@pytest.mark.parametrize('title,excluded', [
    ('PAID INTERN', True), ('Legislative Intern', True), ('Summer Internship', True),
    ('COMM. HOUSE PAID INTERN - MINOR', True), ('International Affairs Director', False),
    ('Internship Coordinator', False), ('Intern Program Manager', False), ('Counsel', False), ('', False)])
def test_role_boundaries(title, excluded):
    assert intern_title(title) is excluded


def test_later_career_and_parallel_substantive_role_are_retained():
    node = {'current_positions': ['Intern for Congress'], 'title': 'Intern'}
    assert not intern_record(node, {'latest_roles': [{'title': 'Chief of Staff'}],
                                    'past_roles': [{'title': 'Intern'}]})
    assert not intern_record(node, {'latest_roles': [{'title': 'Intern'}, {'title': 'Counsel'}]})
    assert not intern_record({'label': 'Intern Example', 'source_summary': 'Intern programme'})


def test_one_exclusion_applies_to_map_office_and_routes_without_deleting_evidence():
    a, i, b = ['person:hill:person:' + key for key in ['a', 'i', 'b']]
    nodes = {key: {'id': key, 'kind': 'person', 'label': label, 'stints': []}
             for key, label in [(a, 'Alice'), (i, 'Iris'), (b, 'Bob')]}
    for key in ['office1', 'office2']:
        nodes[key] = {'id': key, 'kind': 'container', 'container_kind': 'member_office', 'label': key}
    episodes = []
    for key, offices in [(a, ['office1']), (i, ['office1', 'office2']), (b, ['office2'])]:
        for office in offices:
            stint = {'container_id': office, 'title_last': 'Intern' if key == i else 'Counsel',
                     'core_start': '2026-01-01', 'core_end_exclusive': '2026-09-01', 'status': 'current'}
            nodes[key]['stints'].append(stint)
            episodes.append({'person_id': key, 'employer_id': office, 'start': '2026-01-01',
                             'end_exclusive': '2026-09-01', 'confidence': 1, 'provenance': {'fixture': True}})
    original = deepcopy(nodes)
    facts = PersonFacts(nodes, episodes, '2026-09-08')
    assert facts.network_excluded == {i}
    assert i in facts.people and facts.people[i]['latest_roles']
    payload = build_index(nodes, {}, 'scope', facts=facts)['payload']
    assert {r[0] for r in payload['people']} == {a, b}
    assert payload['routing_people_total'] == 2
    assert payload['reconciliation']['network_scope']['excluded_intern_records'] == 1
    assert all(i not in office['roster'] for office in office_index(nodes, facts).values())
    router = AdvocacyRouter(nodes, episodes, 'scope', '2026-09-08')
    assert router.route(a, b)['status'] == 'found'  # The sole bridge is Iris.
    router.person_facts = facts
    router._cache.clear()
    assert router.route(a, b)['status'] == 'unreachable'
    assert router.route(a, i)['status'] == 'unknown_person'
    assert not router.coworkers(a)['coworkers']
    router.overlap_routes = OverlapRoutes(router, facts)
    assert router.route(a, b, {'objective': 'overlap_balanced'})['status'] == 'unreachable'
    assert nodes == original


def test_source_only_intern_is_not_readded_by_contact_catalog():
    records = {'quorum:1': {'name': 'Iris', 'title': 'Summer Intern', 'office': 'Office'}}
    contacts = SimpleNamespace(records=records, get=lambda _: [], coverage={})
    payload = build_index({}, {}, 'scope', contacts=contacts)['payload']
    assert payload['people'] == []
    assert records['quorum:1']['title'] == 'Summer Intern'
