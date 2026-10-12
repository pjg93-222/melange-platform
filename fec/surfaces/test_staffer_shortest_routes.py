"""Optimality is compared with exhaustive simple paths, independent of Dijkstra."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import random
from types import SimpleNamespace

from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.staffer_office_routes import office_index, search_offices
from fec.surfaces.test_staffer_live_service import fixture_graph, ProfilesFixture
from fec.surfaces.staffer_live_service import LiveService


def graph_router(edges, count, **kw):
    nodes = {str(i): {'id': str(i), 'kind': 'person', 'label': f'Person {i}'} for i in range(count)}
    episodes = []
    for k, (a, b, cost) in enumerate(edges):
        for p in (a, b):
            activity = cost == 2
            episodes.append({'person_id': str(p), 'employer_id': ('activity:' if activity else 'office:')+str(k),
                             'start': '2026-01-01', 'end_exclusive': '2026-09-06',
                             'current_evidence': cost == 1, 'as_of': '2026-09-05',
                             'relationship': 'co_reported_lobbying_activity' if activity else 'employment',
                             'provenance': {'person_id': str(p), 'row': k}})
    return AdvocacyRouter(nodes, episodes, 'test', '2026-09-05', **kw)


def exhaustive(edges, origin, targets, objective):
    adjacency = defaultdict(list)
    for a, b, cost in edges:
        adjacency[a].append((b, cost)); adjacency[b].append((a, cost))
    answers = []
    def visit(person, seen, cost):
        if person in targets:
            steps = len(seen)-1
            answers.append((steps, cost) if objective == 'fewest_steps' else (cost, steps))
            return
        for other, weight in adjacency[person]:
            if other not in seen:
                visit(other, seen | {other}, cost+weight)
    visit(origin, {origin}, 0)
    return min(answers) if answers else None


def test_bidirectional_multitarget_matches_exhaustive_paths_for_both_objectives():
    rng = random.Random(45056)
    for trial in range(150):
        count = rng.randint(3, 8)
        edges = [(a,b,rng.choice([1,2,3])) for a in range(count) for b in range(a+1,count) if rng.random()<.35]
        router = graph_router(edges, count)
        targets = set(rng.sample(range(1,count), rng.randint(1,count-1)))
        for objective in ('fewest_steps', 'evidence_cost'):
            expected = exhaustive(edges, 0, targets, objective)
            actual = router.route_many('0', map(str, targets), {'objective':objective})
            assert actual['optimal'] and not actual['search_limited']
            if expected is None:
                assert actual['status'] == 'unreachable'
            else:
                assert actual['status'] == 'found'
                value = (actual['steps'], actual['cost']) if objective == 'fewest_steps' else (actual['cost'], actual['steps'])
                assert value == expected, (trial, edges, targets, actual)
                assert int(actual['target']) in targets
                assert len(actual['node_ids']) == len(set(actual['node_ids']))
                for hop in actual['hops']:
                    assert {hop['source'],hop['target']} in [{str(a),str(b)} for a,b,c in edges]
                    if isinstance(hop['provenance'], list):
                        assert hop['provenance'][0]['person_id'] == hop['source']
                        assert hop['provenance'][1]['person_id'] == hop['target']


def test_fewest_steps_outranks_cheaper_longer_path_and_cost_breaks_equal_step_ties():
    router = graph_router([(0,1,3),(0,2,1),(2,1,1),(0,3,1),(3,4,1),(1,4,3)], 5)
    short = router.route('0','1',{'objective':'fewest_steps'})
    assert (short['steps'],short['cost']) == (1,3)
    cheap = router.route('0','1',{'objective':'evidence_cost'})
    assert (cheap['steps'],cheap['cost']) == (2,2)
    tied = router.route('0','4',{'objective':'fewest_steps'})
    assert (tied['steps'],tied['cost']) == (2,2)


def test_no_work_or_wall_clock_cutoff_even_after_a_candidate_was_found():
    clock = [0]
    class SlowEvidence:
        revision = 'slow'
        def neighbors(self, person, options, budget):
            clock[0] += 100
            return {}
    router = graph_router([(0,1,3),(0,2,1),(2,3,1),(3,1,1),(1,4,3)], 5,
                          clock=lambda:clock[0],activity_provider=SlowEvidence())
    progress=[]
    result = router.route('0','4',{'objective':'fewest_steps','timeout_ms':1,'max_neighbor_checks':1,'max_expansions':1},progress.append)
    assert result['optimal'] and result['complete_search'] and result['steps'] == 2
    assert result['search_elapsed_ms'] >= 100000 and progress


def test_office_chooses_closest_listed_staff_excludes_former_staff_and_is_not_a_hop():
    graph=fixture_graph()
    nodes = graph.nodes
    nodes['office:padilla']={'id':'office:padilla','kind':'container','container_kind':'member_office','label':'SENATOR ALEX PADILLA'}
    def stint(office, status='current', end='2026-04-01'):
        return {'container_id':office,'container_kind':'member_office','core_start':'2025-01-01',
                'core_end_exclusive':end,'status':status,'title_last':'Staff Assistant'}
    for ident,label in [('former','Former Staff'),('close','Close Staff'),('far','Far Staff')]:
        nodes[ident]={'id':ident,'kind':'person','label':label,'stints':[], 'policy_ids':[]}
    nodes['former']['stints']=[stint('office:padilla','departed'),stint('office:test')]
    nodes['close']['stints']=[stint('office:padilla'),stint('office:bridge')]
    nodes['far']['stints']=[stint('office:padilla')]
    nodes['person:stephanie']['stints'].append(stint('office:bridge'))
    service=LiveService('fixture',graph);service.profiles=ProfilesFixture();service._refresh_profiles(force=True);service.ready=True
    options={'include_user_asserted':True}
    result=service.office_route('person:gordon','office:padilla',options)
    assert result['target']=='close' and result['steps']==2 and result['candidate_count']==2
    assert result['office']['records_through']=='2026-03-31'
    assert 'office:padilla' not in result['path']
    assert result['recommended_contact']['title']=='Staff Assistant'
    assert service.offices('Alex Padilla')['nodes'][0]['staff_count']==2
    assert service.route('person:gordon','office:padilla')['status']=='unknown_person'
    assert service.office_route('person:gordon','missing')['status']=='unknown_office'
    assert service.office_route('person:gordon','office:padilla',{'include_review':False})['status']=='no_office_staff'


def test_background_search_reuses_job_and_never_labels_pending_as_unreachable():
    from threading import Event
    gate=Event()
    service=LiveService('fixture',fixture_graph());service.ready=True
    def route(*args):
        gate.wait(2)
        return {'status':'found','optimal':True,'ready':True}
    service.route=route
    try:
        first=service.start_route_search('one','two')
        repeat=service.start_route_search('one','two')
        assert first['search_id']==repeat['search_id']
        assert first['status'] in ('queued','searching')
        gate.set()
        service.route_jobs[first['search_id']]['future'].result(timeout=2)
        assert service.route_search_result(first['search_id'])['optimal']
        assert service.route_search_result('missing')['status']=='search_not_found'
    finally:
        gate.set();service.route_executor.shutdown()
