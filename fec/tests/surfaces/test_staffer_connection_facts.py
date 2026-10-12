from types import SimpleNamespace
from itertools import combinations
import math
import random
import pytest

from fec.surfaces.staffer_connection_facts import (
    DIMENSIONS, PersonFacts, OfficeResolver, employment_dates, identity_profile_episodes, majors, latest_roles)
from fec.surfaces.staffer_overlap_routes import OverlapRoutes
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.staffer_identity_auto import resolve_identities
from fec.surfaces.staffer_office_routes import office_index
from fec.surfaces.staffer_network_explorer import build_index

PROOF = {'path': '/verified/profiles.ndjson', 'sha256': 'a' * 64}


def office(ident='office', label='2026 HON. STEPHEN F. LYNCH'):
    return {'id': ident, 'kind': 'container', 'container_kind': 'member_office',
            'chamber': 'house', 'member_bioguide': 'L000562', 'label': label}


def person(ident, profile=False):
    node = {'id': ident, 'kind': 'person', 'label': 'Grace Ryan', 'provenance': PROOF}
    if profile:
        node.update(profile_records=[{'observed_at': '2026-09-05', 'partition': 'Democratic',
            'current_header_positions': ['Economic Policy Adviser for Rep. Stephen LynchDemocraticMA-08'],
            'employment_history': [{'employer': 'Rep. Stephen Lynch', 'title': 'Economic Policy Adviser',
                                    'date_text': '(Jan 2026 - Present)'}],
            'profile_issue_areas': ['Economics'], 'provenance': PROOF}],
            education=[{'institution': 'Hamline University', 'degree': 'BA, political science, magna cum laude'}])
    return node


def test_date_precision_cannot_invent_overlap_in_an_uncertain_month_or_year():
    dates = employment_dates('(Jan 2026 - Aug 2026)', '2026-09-05')
    assert dates['start'] == '2026-01-31'
    assert dates['end_exclusive'] == '2026-08-02'
    dates = employment_dates('(2026 - Present)', '2026-09-05')
    assert dates['start'] > dates['end_exclusive']
    assert dates['listed_current']
    assert not employment_dates('(Unknown - Present)', '2026-09-05').get('start')


def test_office_name_collision_fails_closed_and_chambers_remain_distinct():
    nodes = {'office': office(), 'duplicate': office('duplicate')}
    assert OfficeResolver(nodes).resolve('Rep. Stephen Lynch') is None
    assert OfficeResolver({'office': office()}).resolve('Sen. Stephen Lynch') is None


def test_profile_work_dates_link_grace_but_not_a_competing_namesake():
    nodes = {'office': office(), 'payroll': person('payroll'), 'politico:profile': person('politico:profile', True)}
    nodes['payroll']['label'] = 'Grace J Ryan'
    payroll = {'person_id': 'payroll', 'employer_id': 'office', 'title': 'ECONOMIC POLICY ADVISOR',
               'start': '2026-01-12', 'end_exclusive': '2026-07-01', 'employment_verified': True, 'provenance': PROOF}
    episodes = [payroll, *identity_profile_episodes(nodes)]
    result = resolve_identities(nodes, episodes, [], {}, [])
    assert len(result['identity_groups']) == 1
    nodes['namesake'] = person('namesake')
    result = resolve_identities(nodes, [*episodes, {**payroll, 'person_id': 'namesake'}], [], {}, [])
    assert not result['identity_groups']


def test_current_directory_takes_precedence_over_payroll_and_keeps_dates_and_history():
    # Spine-namespaced ids: since 2026-09-06 the people index is source-scoped
    # (D-022 item 2) and an id outside a real person source is not a person.
    now, past = 'person:hill:person:now', 'person:hill:person:payroll'
    nodes = {'office': office(), now: person(now, True), past: person(past)}
    nodes[past]['stints'] = [{'container_id': 'office', 'container_kind': 'member_office',
        'status': 'current', 'core_start': '2025-01-01', 'core_end_exclusive': '2026-04-01', 'title_last': 'Old job'}]
    facts = PersonFacts(nodes, as_of='2026-09-05')
    assert facts.people[now]['is_current_staff']
    assert not facts.people[past]['is_current_staff']
    assert facts.people[past]['employment_status'] == 'latest_payroll'
    assert [s['key'] for s in facts.people[now]['sections']] == [k for k, _, _ in DIMENSIONS]
    assert list(facts.features[now]['major']) == ['political science']
    assert facts.features[now]['party']['democratic']['label'] == 'Democratic office / committee'
    roster = office_index(nodes, facts)['office']
    assert set(roster['roster']) == {now}
    assert roster['records_through'] == '2026-09-05'
    index = build_index(nodes, {}, 'r', facts=facts)['payload']
    assert next(r for r in index['people'] if r[0] == now)[9]
    assert not next(r for r in index['people'] if r[0] == past)[9]
    assert index['people_total'] == 2
    # The office is a group, not a person. Both people sort as current: the
    # directory lists one now, the payroll trajectory still marks the other.
    assert index['kind_counts'] == {'person': 2}
    assert all(r[14] for r in index['people'])


def test_repeated_source_records_do_not_inflate_strength():
    a, b = person('a', True), person('b', True)
    a['profile_records'] *= 3
    a['education'] *= 4
    facts = PersonFacts({'a': a, 'b': b, 'office': office()})
    assert all(len(facts.features['a'][k]) == len(facts.features['b'][k]) for k, _, _ in DIMENSIONS)
    assert not majors('JD') and not majors('BA, magna cum laude')
    assert OverlapRoutes.strength({'education': 2}) > OverlapRoutes.strength({'education': 1})
    assert all(DIMENSIONS[i][2] > DIMENSIONS[i+1][2] for i in range(7))


def test_latest_role_uses_the_same_fields_for_directory_and_payroll_and_newest_date_wins():
    directory = {'title':'Assistant','employer':'Office','employer_id':'o','observed_at':'2026-03-01',
                 'basis':'Currently listed in directory','provenance':PROOF}
    payroll = {'title':'Director','employer':'Office','employer_id':'o','status':'current',
               'core_end_exclusive':'2026-07-01','provenance':{'payroll':True}}
    latest = latest_roles([directory], [payroll], [])
    assert len(latest) == 1
    assert latest[0]['title'] == 'Director'
    assert latest[0]['evidence_date'] == '2026-06-30'
    assert latest[0]['status'] == 'last_recorded'
    directory['observed_at'] = '2026-09-05'
    latest = latest_roles([directory], [payroll], [])
    assert latest[0]['title'] == 'Assistant'
    assert latest[0]['status'] == 'reported_current'
    assert {'title','employer','evidence_date','status','status_label','sources'} <= latest[0].keys()


def test_same_role_from_both_sources_combines_support_and_keeps_distinct_jobs():
    directory = {'title':'Policy Adviser','employer':'Office','employer_id':'o','observed_at':'2026-06-30',
                 'basis':'Currently listed in directory','provenance':PROOF}
    payroll = {'title':'POLICY ADVISOR','employer':'Office','employer_id':'o','status':'current',
               'core_end_exclusive':'2026-07-01','provenance':{'payroll':True}}
    latest = latest_roles([directory], [payroll], [])
    assert len(latest) == 1 and latest[0]['status'] == 'reported_current'
    assert len(latest[0]['sources']) == 2
    latest = latest_roles([directory], [payroll,{**payroll,'employer_id':'other','employer':'Other office'}], [])
    assert len(latest) == 2


def test_reported_association_and_past_roles_are_never_promoted_to_current_employment():
    association = {'employer_id':'firm','employer_label':'Firm','as_of':'2026-07-17',
                   'continuous_employment_asserted':False,'provenance':PROOF}
    current_statement = {'basis':'Your current coworker statement','observed_at':'2026-09-05'}
    latest = latest_roles([current_statement], [], [association])
    assert latest[0]['status'] == 'association'
    assert latest[0]['title'] == 'Reported work association'
    past = latest_roles([], [{'title':'Director','employer':'Office','core_end_exclusive':'2024-01-01','status':'departed'}], [])
    assert past[0]['status'] == 'past'
    assert past[0]['evidence_date'] == '2023-12-31'


def test_unresolved_offices_and_compensation_accounts_do_not_create_extra_affinity():
    node = person('p')
    node['stints'] = [{'container_id': ident, 'container_kind': kind, 'core_start':'2020-01-01',
                      'core_end_exclusive':'2020-04-01'} for ident, kind in [('unknown','unresolved'),('intern','member_office')]]
    nodes = {'p':node, 'intern': {'kind':'container','container_kind':'member_office','chamber':'senate','label':'INTERN COMPENSATION - SMITH'}}
    facts = PersonFacts(nodes)
    assert len(facts.people['p']['past_roles']) == 2
    assert not facts.features['p']['past_employment']
    assert not facts.features['p']['member']


def router_with_features(features):
    nodes = {p: {'id': p, 'label': 'Person '+p, 'kind': 'person'} for p in features}
    facts = SimpleNamespace(features={p: {key: {v: {'label': v, 'sources': [PROOF]} for v in values.get(key, [])}
                                         for key, _, _ in DIMENSIONS} for p, values in features.items()})
    router = AdvocacyRouter(nodes, [], 'r', '2026-09-05')
    router.overlap_routes = OverlapRoutes(router, facts)
    return router


def brute_force(features, origin, targets, fewest, balanced=False):
    edges = {p: {} for p in features}
    for a, b in combinations(features, 2):
        counts = {key: len(set(features[a].get(key, [])) & set(features[b].get(key, []))) for key, _, _ in DIMENSIONS}
        strength = sum(w * math.log2(1+counts[k]) for k, _, w in DIMENSIONS)
        if strength:
            edges[a][b] = edges[b][a] = math.ceil(1_000_000*128/strength)
    answers = []
    def visit(p, seen, cost):
        if p in targets:
            steps = len(seen)-1
            answers.append((steps, cost/1_000_000) if fewest else
                           ((cost + steps*1_000_000)/1_000_000, steps) if balanced else (cost/1_000_000, steps))
            return
        for other, distance in edges[p].items():
            if other not in seen:
                visit(other, seen | {other}, cost+distance)
    visit(origin, {origin}, 0)
    return min(answers) if answers else None


def test_weighted_routes_match_independent_exhaustive_paths_for_both_objectives():
    rng = random.Random(8873)
    for trial in range(100):
        n = rng.randint(3, 7)
        features = {str(i): {key: [str(k) for k in range(4) if rng.random() < .2]
                            for key, _, _ in DIMENSIONS} for i in range(n)}
        router = router_with_features(features)
        targets = set(map(str, rng.sample(range(1,n), rng.randint(1,n-1))))
        for objective in ('overlap_strength', 'overlap_fewest_steps', 'overlap_balanced'):
            expected = brute_force(features, '0', targets, objective == 'overlap_fewest_steps', objective == 'overlap_balanced')
            result = router.route_many('0', targets, {'objective': objective, 'include_policy': True,
                'max_neighbor_checks': 1, 'max_expansions': 1, 'timeout_ms': 1})
            assert result['optimal'] and not result['search_limited']
            if expected is None:
                assert result['status'] == 'unreachable'
            else:
                actual = ((result['steps'],result['cost']) if objective == 'overlap_fewest_steps' else
                          (result['balanced_score'],result['steps']) if objective == 'overlap_balanced' else (result['cost'],result['steps']))
                assert actual == pytest.approx(expected), (trial, objective, expected, actual)
                for hop in result['hops']:
                    for overlap in hop['overlaps']:
                        for match in overlap['matches']:
                            assert set(match['sources']) == {hop['source'],hop['target']}


def test_strength_can_choose_longer_route_while_fewest_steps_uses_weak_direct_link():
    router = router_with_features({'a': {'party': ['D'], 'current_employment': ['work1']},
        'b': {'current_employment': ['work1', 'work2']}, 'c': {'party': ['D'], 'current_employment': ['work2']}})
    assert router.route('a', 'c', {'objective': 'overlap_strength'})['node_ids'] == ['a', 'b', 'c']
    assert router.route('a', 'c', {'objective': 'overlap_fewest_steps'})['node_ids'] == ['a', 'c']
    assert router.route('a', 'c', {'objective': 'overlap_strength', 'include_review': False})['status'] == 'unreachable'


def test_balanced_selects_middle_route_between_strongest_and_fewest():
    features = {name:{} for name in ['a','x','y','z','m','t']}
    def edge(a,b,dimensions):
        for key in dimensions:
            for person in (a,b): features[person].setdefault(key,[]).append(a+'-'+b)
    for a,b in [('a','x'),('x','y'),('y','z'),('z','t')]:
        edge(a,b,['current_employment','past_employment','committee','member','subject'])
    edge('a','m',['past_employment']);edge('m','t',['past_employment'])
    edge('a','t',['party'])
    router=router_with_features(features)
    options={'include_policy':True}
    strongest=router.route('a','t',{**options,'objective':'overlap_strength'})
    balanced=router.route('a','t',{**options,'objective':'overlap_balanced'})
    fewest=router.route('a','t',{**options,'objective':'overlap_fewest_steps'})
    assert strongest['node_ids']==['a','x','y','z','t']
    assert balanced['node_ids']==['a','m','t']
    assert fewest['node_ids']==['a','t']
    assert balanced['balanced_score']==6 and balanced['balance_step_penalty']==1
    assert balanced['optimal'] and balanced['complete_search'] and not balanced['search_limited']


def test_proof_pruning_does_not_enumerate_a_dense_dead_end_after_two_step_route_found():
    features={'a':{'current_employment':['origin']},'t':{'current_employment':['target']},
              'bridge':{'current_employment':['origin','target']}}
    for i in range(300):
        features['left'+str(i)]={'current_employment':['origin'],'past_employment':['left']}
        features['right'+str(i)]={'current_employment':['target'],'past_employment':['right']}
    # Stronger edges into each dense group keep a conventional frontier bound
    # below the best two-step route; those extra expansions cannot improve it.
    features['a']['past_employment']=['left'];features['t']['past_employment']=['right']
    router=router_with_features(features)
    result=router.route('a','t',{'objective':'overlap_fewest_steps'})
    assert result['node_ids']==['a','bridge','t'] and result['cost']==2
    assert result['proof_pruned_expansions']>0
    assert result['expanded']==2


def test_office_search_finishes_endpoint_layer_before_expanding_intermediaries():
    features={'a':{'current_employment':['origin']}}
    targets=[]
    for i in range(20):
        target='target'+str(i);targets.append(target)
        features[target]={'current_employment':['office'+str(i)],'party':['shared']}
        for j in range(30):
            features[f'person-{i}-{j}']={'current_employment':['origin','office'+str(i)]}
    for i in range(1000):features['weak'+str(i)]={'party':['shared']}
    router=router_with_features(features)
    result=router.route_many('a',targets,{'objective':'overlap_fewest_steps'})
    assert result['steps']==2 and result['cost']==2
    assert result['target'] in targets and result['optimal']
    assert result['expanded']<=len(targets)+1
    assert result['neighbor_checks']<5000
