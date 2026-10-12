"""payroll_people is a fact of the work index: computed when the index is built, kept on it, and the set every request used to scan for.

A work-time request lets a person be a route node when they are an operational staffer or have payroll evidence in the index.
That set used to be rebuilt by scanning every person's episodes on every request (about 0.2 s on the live index, three times
for the page's default request). It is now computed once, when build_index finishes.
"""
from fec.surfaces.staffer_closeness_routes import ClosenessRoutes, _payroll_people
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.staffer_work_sources import payroll_people
from fec.tests.surfaces.test_staffer_work_time_routes import person

DIRECTORY = {'work_route_source': 'politico_profile_history'}
PAYROLL_ROW = {'method': 'payroll-identity-rows-v1'}
TRAJECTORY = {'source': 'verified_static_trajectory', 'stint': {'title_last': 'Press Secretary', 'container_kind': 'member_office', 'chamber': 'house'}}


def row(who, office, provenance, start='2020-01-01', end='2021-01-01', **extra):
    return {'person_id': who, 'employer_id': office, 'start': start, 'end_exclusive': end, 'employment_verified': True,
            'provenance': provenance, **extra}


def world():
    """a directory only; b payroll rows; c a payroll trajectory; d both kinds; e directory only, elsewhere."""
    nodes = {p: person(p.upper()) for p in 'abcde'}
    nodes.update({'office:x': {'kind': 'container', 'container_kind': 'member_office', 'label': 'Office X'},
                  'office:y': {'kind': 'container', 'container_kind': 'member_office', 'label': 'Office Y'}})
    episodes = [row('a', 'office:x', DIRECTORY), row('b', 'office:x', PAYROLL_ROW), row('c', 'office:x', TRAJECTORY, review_only=True),
                row('d', 'office:x', DIRECTORY), row('d', 'office:y', PAYROLL_ROW, '2021-01-01', '2022-01-01'),
                row('e', 'office:y', DIRECTORY, '2021-01-01', '2022-01-01')]
    return AdvocacyRouter(nodes, episodes, 'payroll-people', '2026-09-10')


def test_the_set_is_the_people_with_a_payroll_sourced_episode_and_is_the_scan_it_replaced():
    router = world()
    index = ClosenessRoutes(router).index(router._options({'objective': 'work_time'}))
    scanned = {p for p, rows in index.source_episodes.items() if any(r['work_source'].startswith('payroll') for r in rows)}
    assert scanned == {'b', 'c', 'd'}, 'payroll rows and payroll trajectories count; a directory row does not'
    assert index.payroll_people == scanned and isinstance(index.payroll_people, frozenset)
    assert payroll_people(index) == scanned
    directory = ClosenessRoutes(router).index(router._options({'objective': 'work_time', 'work_sources': 'directory'}))
    assert directory.payroll_people == frozenset(), 'the directory-only index keeps no payroll evidence'


def test_a_request_reads_the_set_from_the_index_and_never_scans_the_episodes():
    router = world()
    routes = ClosenessRoutes(router)
    options = router._options({'objective': 'work_time'})
    index = routes.index(options)
    scans = []

    class Counting(dict):
        def items(self):
            scans.append(1)
            return super().items()
    index.source_episodes = Counting(index.source_episodes)
    for k in range(4):
        routes.route('a', ('b',), {**options, 'limit': 100 + k})
    assert scans == [], 'payroll_people was fixed when the index was built'


def test_an_index_made_another_way_gets_the_set_computed_once_on_first_use():
    router = world()
    index = ClosenessRoutes(router).index(router._options({'objective': 'work_time'}))
    expected = payroll_people(index)
    del index.payroll_people
    assert _payroll_people(index) == expected and index.payroll_people is _payroll_people(index)


def test_who_may_be_a_route_node_is_unchanged_when_the_operational_list_is_short():
    router = world()
    router.operational_staffer_ids = {'a'}
    # b and d have payroll evidence, so they are allowed with a; e has only a directory row and is not operational
    allowed = router.route('a', 'd', {'objective': 'work_time'})
    assert allowed['status'] == 'found' and allowed['node_ids'][0] == 'a'
    refused = router.route('a', 'e', {'objective': 'work_time'})
    assert refused['status'] == 'insufficient_work_coverage' and refused['unavailable_person_ids'] == ['e']
    refused = router.route('e', 'a', {'objective': 'work_time'})
    assert refused['status'] == 'insufficient_work_coverage' and refused['unavailable_person_ids'] == ['e']
