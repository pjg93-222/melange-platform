from fec.surfaces.staffer_routing import AdvocacyRouter


def person(label):
    return {'kind': 'person', 'label': label, 'member_ids': [], 'committee_ids': [], 'policy_ids': []}


def episode(person_id, employer, start, end, source='politico_profile_history'):
    return {'person_id': person_id, 'employer_id': employer, 'start': start, 'end_exclusive': end,
            'employment_verified': True, 'provenance': {'work_route_source': source}}


def test_staffer_route_uses_only_exact_dated_directory_work_and_time():
    nodes = {p: person(p.upper()) for p in ('a', 'b', 'c', 'd')}
    nodes.update({'office:one': {'kind': 'organization', 'label': 'Office One'},
                  'office:two': {'kind': 'organization', 'label': 'Office Two'}})
    episodes = [episode('a', 'office:one', '2020-01-01', '2021-01-01'),
                episode('b', 'office:one', '2020-01-01', '2020-02-01'),
                episode('c', 'office:one', '2020-01-01', '2021-01-01'),
                episode('a', 'office:two', '2022-01-01', '2023-01-01', 'payroll-identity-rows-v1'),
                episode('d', 'office:two', '2022-01-01', '2023-01-01', 'payroll-identity-rows-v1')]
    router = AdvocacyRouter(nodes, episodes, 'work-time-test', '2026-09-08')
    router.operational_staffer_ids = {'a', 'b', 'c', 'd'}
    short = router.route('a', 'b', {'objective': 'work_time'})
    long = router.route('a', 'c', {'objective': 'work_time'})
    assert short['status'] == long['status'] == 'found'
    assert 0 < short['hops'][0]['work_proximity'] < long['hops'][0]['work_proximity'] < 1
    assert short['hops'][0]['workplaces'][0]['headcount_min'] == 3
    assert long['hops'][0]['workplaces'][0]['headcount_min'] == 2
    assert short['hops'][0]['overlap_duration_days'] == 31
    assert long['hops'][0]['overlap_duration_days'] == 366
    assert long['hops'][0]['cost'] < short['hops'][0]['cost']
    assert router.route('a', 'd', {'objective': 'work_time'})['status'] == 'unreachable'


def test_staffer_route_rejects_nonoperational_endpoint():
    nodes = {'a': person('A'), 'b': person('B'), 'office': {'kind': 'organization', 'label': 'Office'}}
    router = AdvocacyRouter(nodes, [episode('a', 'office', '2020-01-01', '2021-01-01'),
                                    episode('b', 'office', '2020-01-01', '2021-01-01')], 'r', '2026-09-08')
    router.operational_staffer_ids = {'a'}
    result = router.route('a', 'b', {'objective': 'work_time'})
    assert result['status'] == 'insufficient_work_coverage'
    assert result['unavailable_person_ids'] == ['b']
