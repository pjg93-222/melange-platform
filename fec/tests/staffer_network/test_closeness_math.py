"""Independent daily arithmetic and exhaustive route oracles for closeness."""
import itertools
import json
import math
import random

import pytest

from fec.staffer_network.closeness_math import ExposureIndex, TAU, bounded_strength
from fec.staffer_network.member_context import MemberContext
from fec.surfaces.staffer_routing import AdvocacyRouter


def ep(person, office, start=1, end=366, title='Staff', **extra):
    return {'person_id': person, 'employer_id': office, 'start': start, 'end_exclusive': end, 'title': title, **extra}


def meta(kind='member_office', member='M', **extra):
    return {'container_kind': kind, 'chamber': 'house', 'member_bioguide': member, **extra}


def test_size_and_shared_support_with_equal_duration():
    def index(n, title='Staff'):
        return ExposureIndex([ep(str(i), 'office', title=title if i == 0 else 'Staff') for i in range(n)], {'office': meta()})
    small, big, shared = index(20), index(100), index(100, 'SHARED EMPLOYEE')
    weights = [i.pair('0', '1')['strength'] for i in (small,big,shared)]
    assert weights[0] > weights[1] > weights[2] > 0
    assert weights == pytest.approx([1-math.exp(-365/(n-1)*q/TAU) for n,q in [(20,1),(100,1),(100,.02)]])


def test_duplicate_records_do_not_change_headcount_exposure_or_anchors():
    episodes = [ep('a','x'),ep('b','x'),ep('a','y'),ep('c','y')]
    original = ExposureIndex(episodes, {'x':meta(), 'y':meta(member='N')})
    duplicated = ExposureIndex(episodes*4, original.metadata)
    assert duplicated.pair('a','b') == original.pair('a','b')
    assert duplicated.member_anchors('a') == original.member_anchors('a')


def test_daily_exposure_against_independent_discrete_oracle():
    rng = random.Random(71)
    episodes = [ep(str(i), c, rng.randrange(1,6), rng.randrange(7,12)) for i in range(7) for c in ('x','y') if rng.random() < .8]
    index = ExposureIndex(episodes, {c:meta() for c in ('x','y')})
    expected = {}
    for a,b in itertools.combinations(map(str,range(7)),2):
        total = 0
        for day in range(1,12):
            offices = {p:{e['employer_id'] for e in episodes if e['person_id']==p and e['start']<=day<e['end_exclusive']} for p in map(str,range(7))}
            for c in offices[a] & offices[b]:
                count = sum(c in choices for choices in offices.values())
                total += 1/(len(offices[a])*len(offices[b])*(count-1))
        expected[a,b] = total
        assert index.pair(a,b)['effective_exposure_days'] == pytest.approx(total)
    for a in map(str,range(7)):
        assert sum(value for pair,value in expected.items() if a in pair) <= 11


def test_daily_size_and_allocation_changes_are_not_replaced_by_ever_counts():
    index = ExposureIndex([ep('a','x',1,11),ep('b','x',1,11),ep('c','x',6,11),ep('a','y',6,11)], {'x':meta(),'y':meta(member='N')})
    assert index.pair('a','b')['effective_exposure_days'] == pytest.approx(5+5*.5/2)


def test_shared_role_does_not_poison_dedicated_career_or_other_office():
    index = ExposureIndex([ep('a','x',1,11,'SHARED EMPLOYEE'),ep('a','x',11,21),ep('b','x',1,21)], {'x':meta()})
    assert index.pair('a','b')['effective_exposure_days'] == pytest.approx(10*.02+10)


def test_headcount_census_can_weaken_but_cannot_create_a_work_edge():
    index = ExposureIndex([ep('a','x'),ep('b','x')], {'x':meta()}, census_episodes=[ep('c','x')])
    assert index.pair('a','b')['effective_exposure_days'] == pytest.approx(365/2)
    assert index.pair('a','c')['strength'] == 0


def test_committee_does_not_create_member_anchor():
    index = ExposureIndex([ep('a','x'),ep('b','x')], {'x':meta('committee','M')})
    assert index.pair('a','b')['strength'] > 0
    assert index.member_anchors('a') == {}


def test_disconnected_member_prestige_does_not_create_closeness():
    context = MemberContext([('A','B',1),('C','D',1)])
    assert context.score({'A':1},{'C':1})['value'] == 0
    assert context.score({'A':1},{'B':1})['value'] > 0
    assert 0 <= context.score({'A':1,'C':10},{'B':1})['value'] <= 1


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1])
def test_invalid_inputs_fail(value):
    with pytest.raises(ValueError): bounded_strength(value)


def work_router():
    people = ('a','b','c','d','e')
    nodes = {p:{'kind':'person','label':p.upper()} for p in people}
    episodes = []
    # Distinct employers realize an undirected graph; no synthetic friendship
    # weights are injected into the algorithm under test.
    for k,(a,b,days) in enumerate([('a','b',90),('a','c',130),('b','c',30),('b','d',140),('c','d',50),('c','e',30),('e','d',100)]):
        c = 'office'+str(k); nodes[c] = {'kind':'container',**meta(member=str(k))}
        for p in (a,b):
            episodes.append(ep(p,c,'2020-01-01','2020-'+('04-01' if days==90 else '06-01'),provenance={'work_route_source':'politico_profile_history'}))
    return AdvocacyRouter(nodes,episodes,'closeness-test','2026-09-10')


def test_best_five_routes_equal_exhaustive_simple_path_oracle():
    router = work_router()
    answer = router.route('a','d',{'objective':'work_time','route_count':5,'alternative_strategy':'ranked','uncertainty_weight':0})
    index = router.closeness_routes.index(router._options({'objective':'work_time'}))
    oracle=[]
    def visit(path,cost):
        if path[-1]=='d':
            oracle.append((cost,len(path),tuple(path))); return
        for p in sorted(index.neighbors(path[-1])):
            if p in path: continue
            strength=index.pair(path[-1],p)['strength']
            if strength: visit(path+[p],cost-math.log(.8*strength))
    visit(['a'],0)
    oracle.sort()
    assert [r['cost'] for r in answer['routes']] == pytest.approx([r[0] for r in oracle[:5]])
    assert all(len(r['node_ids'])==len(set(r['node_ids'])) for r in answer['routes'])
    assert answer['alternatives_complete']
    json.dumps(answer, allow_nan=False)


def test_office_terminal_anchor_participates_in_search_not_posthoc_resort():
    router=work_router()
    router.nodes['destination']={'kind':'container',**meta(member='T')}
    for p,end in [('b','2020-01-03'),('d','2021-01-01')]:
        row=ep(p,'destination','2020-01-01',end,provenance={'work_route_source':'politico_profile_history'})
        # Reconstruct to exercise the normal episode/date intake.
        episodes=[e for group in router.by_person.values() for e in group]+[row]
        router=AdvocacyRouter(router.nodes,episodes,'r','2026-09-10')
    router.member_context=MemberContext([('T','0',1),('T','1',2)])
    options={'objective':'work_time','route_count':5,'alternative_strategy':'ranked','_target_office':'destination'}
    answer=router.route_many('a',['b','d'],options)
    assert answer['status']=='found'
    assert all(r['office_anchor_strength']>0 for r in answer['routes'])
    assert [r['cost'] for r in answer['routes']]==sorted(r['cost'] for r in answer['routes'])
    assert all(r['connection_score'] <= 1.15*r['route_strength']+1e-12 for r in answer['routes'])
    assert all(len(r['node_ids'])==len(set(r['node_ids'])) for r in answer['routes'])


def test_interns_count_toward_size_but_do_not_supply_scored_anchors():
    index=ExposureIndex([ep('a','x'),ep('b','x'),ep('intern','x',title='INTERN FROM JAN. 12')], {'x':meta()})
    assert index.pair('a','b')['workplaces'][0]['headcount_min']==3
    assert index.member_anchors('intern')=={}
    assert index.pair('a','intern')['strength']==0
