"""A Staffer search that finds no route says why, and names the Ecosystem objective (Paul's ruling, 2026-10-03).

The reason is read from the rows and filters the search itself used, in this order: an office none of whose current
staff has a work record at it, an origin with no usable connection, every possible destination person without one,
or otherwise no path. Found routes, and every other status, are unchanged.

The contract (the four reasons, their sentences and the suggested objective) lives in staffer_unreachable_hint_contract.cjs,
which the page's suite imports too. Every payload in it is asserted here against the producer, on real routers, through
the router, the service and the HTTP handler. The base commit's module is read from git, the way the page suite reads
the page, so "found routes are byte-identical" is a comparison and not a claim.

    python -m pytest fec/tests/surfaces/test_staffer_unreachable_hint.py -q
"""
from collections import Counter
import json
import random
import subprocess
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fec.staffer_network import closeness_math
from fec.surfaces import staffer_closeness_routes as closeness
from fec.surfaces.staffer_closeness_routes import ClosenessRoutes
from fec.surfaces.staffer_live_service import LiveService
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.test_staffer_closeness_bidirectional import query_options, random_queries, settled_options, synthetic_router
from fec.tests.surfaces.test_staffer_route_jobs import Served, wait_until
from fec.tests.surfaces.test_staffer_work_time_routes import episode, person

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CONTRACT = HERE / 'staffer_unreachable_hint_contract.cjs'
AS_OF = '2026-09-10'
# The tip this change was built on (staffer-search-ids-followup): the module as it was before any of it.
BASE = 'f23a40aee4c749bae9db132599a853a52c81d9bf'
NEW_UNREACHABLE_FIELDS = {'unreachable_reason', 'message', 'suggested_objective'}
# What an office result carries since D-086 (an office route searches to its covered staff) and the v4.1 basis of the count: test_staffer_office_covered_staff.py.
OFFICE_COVERAGE_FIELDS = {'uncovered_staff_count', 'uncovered_staff_ids', 'uncovered_basis'}


def load_contract():
    """The shared contract: strict JSON after the last `module.exports =` (its own comment names that text too), read without node."""
    return json.loads(CONTRACT.read_text(encoding='utf-8').rpartition('module.exports =')[2].strip().rstrip(';'))


CASES = {case['name']: case for case in load_contract()['cases']}


# --------------------------------------------------------------------------- the world
# Every edge is two people who worked together at an office of their own for a year, so the graph is exactly this list.
EDGES = [('a1', 'a2'), ('a2', 'a3'), ('a1', 'a0'), ('a3', 'a4'), ('b1', 'b2')]
UI = {'objective': 'work_time', 'route_count': 3, 'alternative_strategy': 'distinct', 'work_sources': 'payroll_and_directory',
      'include_historical': True, 'include_review': True, 'include_unresolved': False, 'include_policy': False,
      'include_user_asserted': True}


def world(operational=None, apart=()):
    """Two components (a0-a1-a2-a3-a4 and b1-b2), people in no record (iso, iso2) and one alone at an office (solo).

    `apart` names people who worked at a1's and a2's office ten years before them: neighbours of both who never stood there
    with either of them, so their pairs have no overlap (the search prices them only if it does not look first)."""
    people = sorted({p for edge in EDGES for p in edge} | {'iso', 'iso2', 'solo'} | set(apart))
    nodes = {p: person(p.upper()) for p in people}
    for office in ['office:' + a + b for a, b in EDGES] + ['office:solo', 'office:profile:nobody']:
        nodes[office] = {'kind': 'container', 'container_kind': 'member_office', 'member_bioguide': 'M' + office[7:],
                         'label': 'Office ' + office[7:]}
    episodes = [episode(p, 'office:' + a + b, '2020-01-01', '2021-01-01') for a, b in EDGES for p in (a, b)]
    episodes.append(episode('solo', 'office:solo', '2020-01-01', '2021-01-01'))
    episodes.extend(episode(p, 'office:a1a2', '2010-01-01', '2011-01-01') for p in apart)
    router = AdvocacyRouter(nodes, episodes, 'unreachable-hint', AS_OF)
    if operational is not None:
        router.operational_staffer_ids = set(operational)
    return router


# name: (origin, destination, extra options). A destination that is a person is a string; an office is (office id, its staff).
SCENARIOS = {
    'office_without_work_records': ('a1', ('office:profile:nobody', ('b1', 'b2')), {}),
    'origin_without_work_connections': ('iso', 'a3', {}),
    'origin_without_work_connections_office': ('iso', ('office:b1b2', ('b1', 'b2')), {}),
    'target_without_work_connections': ('a1', 'iso', {}),
    'target_without_work_connections_office': ('a1', ('office:solo', ('solo',)), {}),
    'no_path_in_work_records': ('a1', 'b1', {}),
    'no_path_in_work_records_excluded': ('a1', 'a3', {'avoid_people': ('a2',)}),
    'no_path_in_work_records_office': ('a1', ('office:b1b2', ('b1', 'b2')), {}),
    'no_path_in_work_records_office_excluded': ('a1', ('office:a3a4', ('a3', 'a4')), {'avoid_people': ('a2',)}),
    'insufficient_work_coverage': ('a1', 'a3', {}),
}
RESTRICTED = {'insufficient_work_coverage'}   # run where a3 has no payroll record and is not an operational staffer


def router_for(name):
    router = world(operational='a0 a1 a2 a4 b1 b2 iso iso2 solo'.split()) if name in RESTRICTED else world()
    router.office_index = {}   # the office index the service reads: a directory office, its staff current profiles
    for _, destination, _ in SCENARIOS.values():
        if not isinstance(destination, str):
            office, staff = destination
            router.office_index[office] = {
                'id': office, 'kind': 'office', 'label': 'Office ' + office[7:], 'staff_count': len(staff),
                'records_through': '2026-09-01', 'roster_basis': 'Currently listed directory staff',
                'roster': {p: {'person_id': p, 'label': p.upper(), 'basis': 'current_profile'} for p in staff}}
    return router


def via_router(router, name):
    """The router as the page reaches it: every option the page sends, the default three distinct routes."""
    origin, destination, extra = SCENARIOS[name]
    if isinstance(destination, str):
        return router.route(origin, destination, {**UI, **extra})
    office, staff = destination
    return router.route_many(origin, staff, {**UI, **extra, '_target_office': office})


def make_service(router):
    service = LiveService('fixture', SimpleNamespace(nodes={}))
    service.ready, service.stage = True, 'Ready'
    service.router, service.revision = router, router.revision
    return service


def via_service(service, name):
    origin, destination, extra = SCENARIOS[name]
    if isinstance(destination, str):
        return service.route(origin, destination, {**UI, **extra})
    return service.office_route(origin, destination[0], {**UI, **extra})


def via_http(server, name):
    """The page's own requests: start a background search, poll it, and read the JSON the handler serialises."""
    origin, destination, extra = SCENARIOS[name]
    query = {**UI, **extra, 'origin': origin, 'async': 1, 'holder': 'page-one'}
    query.update({'include_historical': 1, 'include_review': 1, 'include_policy': 0, 'include_unresolved': 0, 'include_user_asserted': 1})
    query['avoid_people'] = ','.join(extra.get('avoid_people', ()))
    if isinstance(destination, str):
        path, query['target'] = '/api/staffer-route', destination
    else:
        path, query['office'] = '/api/staffer-office-route', destination[0]
    pairs = '&'.join('%s=%s' % (key, str(value).replace(':', '%3A')) for key, value in query.items())
    status, answer = server.request('GET', path + '?' + pairs)
    assert status == 200
    key = answer['search_id']
    outcome = {}

    def done():
        code, current = server.request('GET', '/api/staffer-route-result?id=' + key)
        outcome['value'] = current
        return code == 200 and current['status'] not in ('queued', 'searching')
    assert wait_until(done, 20), outcome
    return outcome['value']


def direct(router, name):
    """The single search behind a scenario: ClosenessRoutes.route with the options route_many would hand it."""
    origin, destination, extra = SCENARIOS[name]
    targets = (destination,) if isinstance(destination, str) else destination[1]
    options = {} if isinstance(destination, str) else {'_target_office': destination[0]}
    if 'avoid_people' in extra:
        options['_excluded_nodes'] = extra['avoid_people']
    return origin, targets, settled_options(router, **options)


@pytest.fixture
def stack():
    made = []

    def make(router):
        service = make_service(router)
        made.append((service, Served(service)))
        return made[-1]
    yield make
    for service, server in made:
        server.close()
        service.route_executor.shutdown(wait=True, cancel_futures=True)


def mismatches(produce, names=None):
    """Every way an answer differs from the contract: a field's value, or a reason on a result that has none."""
    wrong = []
    for name in names or CASES:
        result = produce(name)
        for key, expected in CASES[name]['payload'].items():
            if result.get(key) != expected:
                wrong.append((name, key, result.get(key), expected))
        if 'unreachable_reason' not in CASES[name]['payload'] and 'unreachable_reason' in result:
            wrong.append((name, 'unreachable_reason', result['unreachable_reason'], None))
    return wrong


def fresh_produce():
    """A producer over routers that have answered nothing yet: a router remembers its answers, mutations included."""
    routers = {name: router_for(name) for name in CASES}
    return lambda name: via_router(routers[name], name)


# --------------------------------------------------------------------------- the four reasons, against the contract
def test_the_contract_names_every_scenario_and_every_reason():
    assert set(CASES) == set(SCENARIOS)
    assert {c['payload'].get('unreachable_reason') for c in CASES.values()} - {None} == set(closeness.UNREACHABLE_MESSAGES)
    assert {c['payload']['suggested_objective'] for c in CASES.values()} == {closeness.SUGGESTED_OBJECTIVE}
    assert {c['mode'] for c in CASES.values()} == {'person', 'office'}
    for name, case in CASES.items():   # the page is told which kind of destination each payload answers
        assert (case['mode'] == 'office') == (not isinstance(SCENARIOS[name][1], str)), name


def test_each_reason_on_a_constructed_router_matches_the_contract():
    assert mismatches(fresh_produce()) == []


def test_every_reason_reaches_the_page_through_the_service():
    services = {name: make_service(router_for(name)) for name in CASES}
    assert mismatches(lambda name: via_service(services[name], name)) == []
    answer = via_service(services['no_path_in_work_records'], 'no_path_in_work_records')
    assert answer['found'] is False and answer['ready'] is True and answer['status'] == 'unreachable'
    assert answer['routes'] == [] and answer['path'] == []


def test_every_reason_reaches_the_page_over_http_in_the_json_the_handler_serves(stack):
    served = {name: stack(router_for(name))[1] for name in CASES}
    assert mismatches(lambda name: via_http(served[name], name)) == []


# --------------------------------------------------------------------------- the explanation is the message (the re-check's finding A4)
# An unreachable answer that carries a reason says it in `message`; its `explanation` (the text under "How this route was chosen", and what
# any other consumer of the payload reads) is that same text, not the generic sentence that was written before the reasons existed. An answer
# with no reason keeps the generic sentence, and so does every other status.
GENERIC = ('No supported path exists in the currently indexed evidence under these filters. This does not establish that no real-world '
           'introduction is possible.')
ALTERNATIVES = ' Alternatives are found by excluding contacts used in earlier choices'
REASONED = [name for name, case in CASES.items() if 'unreachable_reason' in case['payload']]


def asked(service, name, **override):
    origin, destination, extra = SCENARIOS[name]
    options = {**UI, **extra, **override}
    if isinstance(destination, str):
        return service.route(origin, destination, options)
    return service.office_route(origin, destination[0], options)


def explanation_failures(produce):
    """Every unreachable answer with a reason whose explanation is not its message (nor the contract's message)."""
    wrong = []
    for name in REASONED:
        for override in ({}, {'route_count': 1}, {'alternative_strategy': 'independent'}, {'alternative_strategy': 'ranked'}):
            answer = produce(name, **override)
            if not (answer['status'] == 'unreachable' and answer['explanation'] == answer['message'] == CASES[name]['payload']['message']):
                wrong.append((name, override, answer['status'], answer['explanation'][:70]))
    return wrong


def test_the_explanation_of_an_unreachable_answer_with_a_reason_is_its_message_for_every_reason_and_strategy():
    services = {name: make_service(router_for(name)) for name in REASONED}
    assert len(REASONED) == 9 and 'insufficient_work_coverage' not in REASONED
    assert explanation_failures(lambda name, **override: asked(services[name], name, **override)) == []


def test_the_explanation_of_an_unreachable_answer_with_a_reason_is_its_message_over_http(stack):
    served = {name: stack(router_for(name))[1] for name in REASONED}
    for name in REASONED:
        answer = via_http(served[name], name)
        assert answer['status'] == 'unreachable' and answer['unreachable_reason']
        assert answer['explanation'] == answer['message'] == CASES[name]['payload']['message'], name


def test_an_unreachable_answer_without_a_reason_and_every_other_status_keep_their_explanation():
    router = world()
    service = make_service(router)
    # Ecosystem has no reasons: its unreachable answer keeps the generic sentence, and the distinct portfolio's note after it
    ecosystem = {**UI, 'objective': closeness.SUGGESTED_OBJECTIVE}
    single = service.route('a1', 'b1', {**ecosystem, 'route_count': 1})
    assert single['status'] == 'unreachable' and 'unreachable_reason' not in single and single['explanation'] == GENERIC
    portfolio = service.route('a1', 'b1', ecosystem)
    assert portfolio['status'] == 'unreachable' and 'unreachable_reason' not in portfolio
    assert portfolio['explanation'].startswith(GENERIC + ALTERNATIVES) and portfolio['explanation'] != portfolio.get('message')
    # the Staffer answers that are not unreachable
    found = service.route('a1', 'a3', {**UI, 'route_count': 1})
    assert found['status'] == 'found' and found['explanation'].startswith('Ranks complete paths')
    unknown = service.route('a1', 'nobody-at-all', {**UI, 'route_count': 1})
    assert unknown['status'] == 'unknown_person' and unknown['explanation'].startswith('No search was run')
    restricted = make_service(router_for('insufficient_work_coverage'))
    coverage = asked(restricted, 'insufficient_work_coverage', route_count=1)
    assert coverage['status'] == 'insufficient_work_coverage' and coverage['explanation'] == coverage['message']
    distinct = asked(restricted, 'insufficient_work_coverage')
    assert distinct['explanation'].startswith(distinct['message'] + ALTERNATIVES), 'a coverage answer is worded as it always was'


def test_a_payload_with_a_status_of_unreachable_and_a_reason_but_no_message_keeps_the_generic_sentence(monkeypatch):
    """The page reads its sentence from `message` only when the reason and the message are both there, and so does the explanation."""
    service = make_service(router_for('no_path_in_work_records'))
    real = ClosenessRoutes.route

    def without_message(self, *args, **kwargs):
        result = real(self, *args, **kwargs)
        return {k: v for k, v in result.items() if k != 'message'} if result['status'] == 'unreachable' else result
    monkeypatch.setattr(ClosenessRoutes, 'route', without_message)
    answer = asked(service, 'no_path_in_work_records', route_count=1)
    assert answer['status'] == 'unreachable' and answer['unreachable_reason'] and 'message' not in answer and answer['explanation'] == GENERIC


def test_the_explanation_check_has_teeth(monkeypatch):
    """Negative control: the service as it was before the change (the generic sentence for every unreachable answer) fails it, for all nine."""
    real = LiveService._route_response

    def generic(self, router, result, started):
        answer = real(self, router, result, started)
        return {**answer, 'explanation': GENERIC} if result['status'] == 'unreachable' else answer
    monkeypatch.setattr(LiveService, '_route_response', generic)
    services = {name: make_service(router_for(name)) for name in REASONED}
    failures = explanation_failures(lambda name, **override: asked(services[name], name, **override))
    assert {name for name, *_ in failures} == set(REASONED) and len(failures) == 4 * len(REASONED)


def test_the_page_selector_and_the_server_agree_on_the_ecosystem_objective():
    html = (ROOT / 'fec/surfaces/staffer_live/index.html').read_text(encoding='utf-8')
    assert '<option value="%s">Ecosystem' % closeness.SUGGESTED_OBJECTIVE in html
    assert AdvocacyRouter._options({'objective': closeness.SUGGESTED_OBJECTIVE})['objective'] == closeness.SUGGESTED_OBJECTIVE


def test_the_contract_check_has_teeth(monkeypatch):
    assert mismatches(fresh_produce()) == []
    with monkeypatch.context() as patched:   # a sentence changes
        patched.setitem(closeness.UNREACHABLE_MESSAGES, 'no_path_in_work_records', 'No route.')
        assert {m[0] for m in mismatches(fresh_produce())} == {'no_path_in_work_records', 'no_path_in_work_records_excluded'}
    with monkeypatch.context() as patched:   # an office sentence changes
        patched.setitem(closeness.OFFICE_UNREACHABLE_MESSAGES, 'target_without_work_connections', 'No route.')
        assert {m[0] for m in mismatches(fresh_produce())} == {'target_without_work_connections_office'}
    with monkeypatch.context() as patched:   # the clause for people the reader excluded is lost
        patched.setattr(closeness, 'EXCLUSION_CLAUSE', '')
        assert {m[0] for m in mismatches(fresh_produce())} == {'no_path_in_work_records_excluded', 'no_path_in_work_records_office_excluded'}
    with monkeypatch.context() as patched:   # the suggestion is wrong, on every result that carries it
        patched.setattr(closeness, 'SUGGESTED_OBJECTIVE', 'fewest_steps')
        assert {m[0] for m in mismatches(fresh_produce())} == set(CASES)
    with monkeypatch.context() as patched:   # every person looks connected, so only the last reason can hold
        patched.setattr(closeness, '_has_usable_hop', lambda *args: True)
        assert {m[0] for m in mismatches(fresh_produce())} == {
            'origin_without_work_connections', 'origin_without_work_connections_office',
            'target_without_work_connections', 'target_without_work_connections_office'}
    with monkeypatch.context() as patched:   # an office is described as if it were a person
        patched.setattr(closeness, 'OFFICE_UNREACHABLE_MESSAGES', {})
        assert {m[0] for m in mismatches(fresh_produce())} == {
            'target_without_work_connections_office', 'no_path_in_work_records_office', 'no_path_in_work_records_office_excluded'}


def test_the_first_reason_that_applies_wins():
    router = world()
    # An office nobody on record works at, from a person in no record: the office comes first, then the origin.
    assert router.route_many('iso', ('b1', 'b2'), {**UI, '_target_office': 'office:profile:nobody'})['unreachable_reason'] == 'office_without_work_records'
    # Both ends in no record: the origin is named, then the destination.
    assert router.route('iso', 'iso2', UI)['unreachable_reason'] == 'origin_without_work_connections'
    assert router.route('a1', 'iso2', UI)['unreachable_reason'] == 'target_without_work_connections'
    # An office that does have staff on record, none of them connected, and an origin that is: the destination is named.
    assert router.route_many('a1', ('solo',), {**UI, '_target_office': 'office:solo'})['unreachable_reason'] == 'target_without_work_connections'


@pytest.mark.parametrize('request_options,reason', [
    ({'_excluded_contexts': ('office:a1a0',)}, 'origin_without_work_connections'),   # a0's one connection is a workplace the request excludes
    ({'_excluded_edges': (('a0', 'a1'),)}, 'origin_without_work_connections'),       # ... or an edge it excludes
    ({'_excluded_nodes': ('a1',)}, 'origin_without_work_connections'),               # ... or a person it excludes
    ({}, None)])                                                                      # nothing excluded: a0 reaches a3
def test_a_connection_the_requests_own_filters_remove_is_not_usable(request_options, reason):
    router = world()
    result = ClosenessRoutes(router).route('a0', ('a3',), settled_options(router, **request_options))
    assert result.get('unreachable_reason') == reason
    assert result['status'] == ('unreachable' if reason else 'found')


def test_people_the_reader_excluded_are_named_only_where_they_may_be_the_reason():
    router = world()
    plain = router.route('a1', 'a3', {**UI, 'avoid_people': ('a2',)})
    assert plain['unreachable_reason'] == 'no_path_in_work_records' and plain['message'].endswith(closeness.EXCLUSION_CLAUSE + '.')
    # Excluding someone who is not in the way does not change the answer, and the clause is not a claim about them.
    assert router.route('a1', 'b1', {**UI, 'avoid_people': ('a4',)})['message'].endswith(closeness.EXCLUSION_CLAUSE + '.')
    assert closeness.EXCLUSION_CLAUSE not in router.route('a1', 'b1', UI)['message']
    # The other three reasons say "this search can use", which already covers what the reader excluded.
    isolated = router.route('a0', 'a3', {**UI, 'avoid_people': ('a1',)})
    assert isolated['unreachable_reason'] == 'origin_without_work_connections'
    assert closeness.EXCLUSION_CLAUSE not in isolated['message'] and 'this search can use' in isolated['message']


def test_found_and_unknown_results_carry_none_of_the_new_fields():
    router = world()
    found = router.route('a1', 'a3', UI)
    assert found['status'] == 'found' and not NEW_UNREACHABLE_FIELDS & set(found)
    assert all(not NEW_UNREACHABLE_FIELDS & set(route) for route in found['routes'])
    unknown = router.route('nobody-at-all', 'a3', UI)
    assert unknown['status'] == 'unknown_person' and not NEW_UNREACHABLE_FIELDS & set(unknown)
    covered = world(operational='a0 a1 a2 a4'.split()).route('a1', 'a3', UI)
    assert covered['status'] == 'insufficient_work_coverage'
    assert covered['suggested_objective'] == 'evidence_cost' and 'unreachable_reason' not in covered
    assert covered['unavailable_person_ids'] == ['a3']
    assert covered['message'] == CASES['insufficient_work_coverage']['payload']['message']


def test_a_cached_answer_carries_the_same_reason():
    router = world()
    routes = ClosenessRoutes(router)
    options = settled_options(router)
    first = routes.route('a1', ('b1',), options)
    again = routes.route('a1', ('b1',), options)
    assert not first['cache_hit'] and again['cache_hit']
    assert {k: again[k] for k in NEW_UNREACHABLE_FIELDS} == {k: first[k] for k in NEW_UNREACHABLE_FIELDS}


# --------------------------------------------------------------------------- against the base commit
def load_base():
    try:
        source = subprocess.run(['git', '-C', str(ROOT), 'show', BASE + ':fec/surfaces/staffer_closeness_routes.py'],
                                capture_output=True, check=True).stdout.decode('utf-8')
    except (OSError, subprocess.CalledProcessError):
        return None
    module = types.ModuleType('fec.surfaces.staffer_closeness_routes_base')
    module.__package__, module.__file__ = 'fec.surfaces', 'staffer_closeness_routes_base.py'
    exec(compile(source, 'staffer_closeness_routes@' + BASE[:9], 'exec'), module.__dict__)
    return module


@pytest.fixture(scope='module')
def base():
    module = load_base()
    if module is None:
        pytest.skip('the base commit %s is not in this checkout' % BASE[:9])
    return module


# pairs_scored counts the pairs whose arithmetic ran (the cold-path change): a result has it now and the base commit's has not, and it is no part of the answer.
def without_timing(value):
    if isinstance(value, dict):
        return {k: without_timing(v) for k, v in value.items() if k not in ('search_elapsed_ms', 'pairs_scored')}
    if isinstance(value, list):
        return [without_timing(v) for v in value]
    return value


def dump(value):
    return json.dumps(without_timing(value), sort_keys=True)


def without(value, names):
    """The value with every key in `names` removed, at any depth."""
    if isinstance(value, dict):
        return {k: without(v, names) for k, v in value.items() if k not in names}
    if isinstance(value, list):
        return [without(v, names) for v in value]
    return value


def compare(new, old, tally, office=False, narrowed=False):
    """Found and unknown results equal the base's byte for byte; the others differ by exactly the new fields.

    An office result also carries the two fields of the covered-staff rule (D-086). Where that rule narrowed the staff, `old`
    is the base's answer for the covered staff alone, and the two differ in target_count (the staff named, and the staff searched).
    """
    assert new['status'] == old['status']
    tally[new['status']] += 1
    if office and new['status'] != 'unknown_person':
        assert OFFICE_COVERAGE_FIELDS <= set(new) and not OFFICE_COVERAGE_FIELDS & set(old)
        new = without(new, OFFICE_COVERAGE_FIELDS | ({'target_count'} if narrowed else set()))
        old = without(old, {'target_count'} if narrowed else set())
    if new['status'] == 'unreachable':
        assert NEW_UNREACHABLE_FIELDS <= set(new) and not NEW_UNREACHABLE_FIELDS & set(old)
        assert dump({k: v for k, v in new.items() if k not in NEW_UNREACHABLE_FIELDS}) == dump(old)
    elif new['status'] == 'insufficient_work_coverage':
        assert 'suggested_objective' in new and 'suggested_objective' not in old and 'unreachable_reason' not in new
        assert dump({k: v for k, v in new.items() if k != 'suggested_objective'}) == dump(old)
    else:
        assert dump(new) == dump(old)
        assert not NEW_UNREACHABLE_FIELDS & set(new)


def test_results_equal_the_base_commits_except_for_the_new_fields(base):
    tally = Counter()
    for seed, quantize, restricted in ((1, 0, False), (2, 0, True), (3, 90, False)):
        router, ids, offices = synthetic_router(seed, people=150 + 30 * seed, offices=14, quantize=quantize)
        for lone in ('p90001', 'p90002'):   # in no record at all
            router.nodes[lone] = person('Person ' + lone)
        dropped = sorted(random.Random(seed).sample(ids, 8))
        if restricted:   # eight people with no payroll record who are not operational staffers: their routes lack coverage
            router.operational_staffer_ids = (set(ids) - set(dropped)) | {'p90001', 'p90002'}
        new, old = ClosenessRoutes(router), base.ClosenessRoutes(router)
        rng = random.Random(seed)
        queries = [(o, t, off, query_options(router, ids, offices, rng, o, off)) for o, t, off in random_queries(router, ids, offices, rng, 22, 12)]
        outsiders = tuple(p for p in ids if not any(e['employer_id'] == offices[0] for e in router.by_person.get(p, [])))[:10]
        queries += [(ids[0], (ids[5],), None, settled_options(router)),
                    ('p90001', (ids[5],), None, settled_options(router, route_count=3)),
                    (ids[5], ('p90001',), None, settled_options(router)),
                    ('p90001', ('p90002',), None, settled_options(router)),
                    (ids[0], outsiders, offices[0], settled_options(router, _target_office=offices[0])),
                    ('p90001', outsiders, offices[0], settled_options(router, _target_office=offices[0], route_count=5))]
        queries += [(ids[1], (d,), None, settled_options(router)) for d in dropped[:4]]
        operational = getattr(router, 'operational_staffer_ids', None)
        covered = lambda p: operational is None or p in operational
        for origin, targets, office, options in queries:
            narrowed = tuple(p for p in targets if covered(p)) if office and covered(origin) else targets
            narrowed = narrowed or targets   # an office with no covered staff is refused, as before
            compare(new.route(origin, targets, options), old.route(origin, narrowed, options), tally,
                    office=bool(office), narrowed=narrowed != targets)
    print('compared against %s: %s' % (BASE[:9], dict(tally)))
    assert tally['found'] >= 40 and tally['unreachable'] >= 12 and tally['insufficient_work_coverage'] >= 4


def test_the_checks_leave_the_searchs_own_counters_alone(base):
    checked = 0
    for name, case in CASES.items():
        if case['payload']['status'] != 'unreachable':
            continue
        router = router_for(name)
        request = direct(router, name)
        new, old = ClosenessRoutes(router).route(*request), base.ClosenessRoutes(router).route(*request)
        assert (new['expanded'], new['neighbor_checks']) == (old['expanded'], old['neighbor_checks']), name
        checked += 1
    assert checked == 9


# --------------------------------------------------------------------------- what the checks cost
def work_of(module, router, request, overlapping_only=False):
    """(result, rows built, pairs priced) for one request on a cold ClosenessRoutes of `module`. A pair is priced by pair() or by pair_row(), the part of it a row reads.

    With `overlapping_only` a pair counts only if its two people stood in one workplace together for a day or more
    (ExposureIndex.overlaps). Those are the pairs the cold-path change still prices; the base commit prices the others too, and
    their strength is always 0.
    """
    counts = Counter()
    build, pair, pair_row = module._Neighbors.build, closeness_math.ExposureIndex.pair, closeness_math.ExposureIndex.pair_row
    overlaps = closeness_math.ExposureIndex.overlaps

    def counting_build(self, person_id):
        counts['rows'] += 1
        return build(self, person_id)

    def counting_pair(self, left, right):
        if not overlapping_only or overlaps(self, left, right):
            counts['pairs'] += 1
        return pair(self, left, right)

    def counting_pair_row(self, left, right):
        if not overlapping_only or overlaps(self, left, right):
            counts['pairs'] += 1
        return pair_row(self, left, right)
    with patch.object(module._Neighbors, 'build', counting_build), patch.object(closeness_math.ExposureIndex, 'pair', counting_pair), \
            patch.object(closeness_math.ExposureIndex, 'pair_row', counting_pair_row):
        result = module.ClosenessRoutes(router).route(*request)
    return result, counts['rows'], counts['pairs']


def check_the_checks_build_no_rows_and_price_no_pairs(base):
    for name, case in CASES.items():
        if case['payload']['status'] != 'unreachable':
            continue
        router = router_for(name)
        request = direct(router, name)
        new, new_rows, new_pairs = work_of(closeness, router, request)
        old, old_rows, old_pairs = work_of(base, router, request, overlapping_only=True)
        assert new['status'] == old['status'] == 'unreachable'
        print('%-42s rows %d -> %d, overlapping pairs %d -> %d' % (name, old_rows, new_rows, old_pairs, new_pairs))
        # Rows: exactly the base commit's. Pairs: exactly the ones the base commit priced between people who stood in one
        # workplace together. The cold-path change skips the others before it prices them, so a count that is merely "not
        # more" than the base's would let an explanation price extra pairs for as many as the skipped ones.
        assert new_rows == old_rows and new_pairs == old_pairs, name


def test_the_checks_build_no_rows_the_search_did_not_and_price_no_pairs(base):
    check_the_checks_build_no_rows_and_price_no_pairs(base)


def pricing_explainer(monkeypatch):
    """An explanation that prices one pair of its own, the way a regression would: the pair itself is one the search priced already."""
    original = closeness._explain_unreachable

    def pricing(neighbors, *args):
        neighbors.cache.index.pair_row('a1', 'a2')
        return original(neighbors, *args)
    monkeypatch.setattr(closeness, '_explain_unreachable', pricing)


def test_an_explanation_that_prices_an_extra_pair_fails_the_exact_count(base, monkeypatch):
    check_the_checks_build_no_rows_and_price_no_pairs(base)
    pricing_explainer(monkeypatch)
    with pytest.raises(AssertionError):
        check_the_checks_build_no_rows_and_price_no_pairs(base)


def test_the_exact_count_sees_what_a_not_more_than_count_could_not(base, monkeypatch):
    # In the worlds above every neighbour overlaps, so the base commit's count has no slack. Here z1 and z2 shared a1's and a2's
    # office a decade before them: the base prices their pairs with a1 and a2 (strength 0), the cold-path change does not, and
    # "never more than the base" would let an explanation price as many extra pairs as that.
    router = world(apart=('z1', 'z2'))
    request = ('a1', ('b1',), settled_options(router))
    old, old_rows, old_pairs = work_of(base, router, request)
    old_overlapping = work_of(base, router, request, overlapping_only=True)[2]
    new, new_rows, new_pairs = work_of(closeness, router, request)
    assert new['unreachable_reason'] == 'no_path_in_work_records' and old['status'] == 'unreachable'
    assert old_pairs > old_overlapping, 'the base commit priced pairs that could not overlap'
    assert new_rows == old_rows and new_pairs == old_overlapping and new_pairs < old_pairs
    pricing_explainer(monkeypatch)
    extra, extra_rows, extra_pairs = work_of(closeness, router, request)
    assert extra_rows == old_rows and extra_pairs == new_pairs + 1
    assert extra_pairs <= old_pairs, 'the old assertion (not more than the base) passes this explanation'
    assert extra_pairs != old_overlapping, 'the exact count does not'


def test_a_row_the_search_never_reached_is_built_once_and_only_until_a_connected_destination_is_found(base):
    # The origin's component is two people, so the search ends before it reaches the office's staff. The staff member with
    # the longest service has no connection (nobody else was there in those years) and is the first the search expands;
    # the others were never reached. Reading the first costs nothing, the second costs one row, and nothing after it is read.
    nodes = {p: person(p.upper()) for p in ('x', 'y', 's0', 's1', 's2', 's3')}
    nodes['office:big'] = {'kind': 'container', 'container_kind': 'member_office', 'member_bioguide': 'MBIG', 'label': 'Big'}
    episodes = [episode('x', 'office:xy', '2020-01-01', '2021-01-01'), episode('y', 'office:xy', '2020-01-01', '2021-01-01'),
                episode('s0', 'office:big', '2000-01-01', '2005-01-01')]
    episodes += [episode(p, 'office:big', '2020-01-01', '2021-01-01') for p in ('s1', 's2', 's3')]
    router = AdvocacyRouter(nodes, episodes, 'reach', AS_OF)
    request = ('x', ('s0', 's1', 's2', 's3'), settled_options(router, _target_office='office:big'))
    new, new_rows, new_pairs = work_of(closeness, router, request)
    old, old_rows, old_pairs = work_of(base, router, request)
    assert new['unreachable_reason'] == 'no_path_in_work_records'
    print('multi-seed office: rows %d -> %d, pairs %d -> %d' % (old_rows, new_rows, old_pairs, new_pairs))
    assert new_rows - old_rows == 1 and new_pairs - old_pairs <= 3


def test_the_explanation_is_a_sliver_of_a_search_that_had_to_exhaust_a_component(monkeypatch):
    # Two worlds of 450 people that share no office: the search ends only when one side has expanded its whole world.
    router, ids, offices = synthetic_router(5, people=450, offices=15)
    other, other_ids, _ = synthetic_router(6, people=450, offices=15)
    elsewhere = lambda name: 'q' + name[1:] if name.startswith('p') else 'office:q' + name[7:]
    for name, node in other.nodes.items():
        router.nodes[elsewhere(name)] = node
    for name, rows in other.by_person.items():
        router.by_person[elsewhere(name)] = [{**row, 'person_id': elsewhere(name), 'employer_id': elsewhere(row['employer_id'])} for row in rows]
    far = elsewhere(other_ids[0])
    spent = []
    original = closeness._explain_unreachable

    def timed(*args):
        started = time.perf_counter()
        try:
            return original(*args)
        finally:
            spent.append(time.perf_counter() - started)
    monkeypatch.setattr(closeness, '_explain_unreachable', timed)
    started = time.perf_counter()
    result = ClosenessRoutes(router).route(ids[0], (far,), settled_options(router))
    total = time.perf_counter() - started
    assert result['unreachable_reason'] == 'no_path_in_work_records' and result['expanded'] > 400
    print('whole request %.1f ms (%d people expanded), the explanation %.3f ms' % (total * 1000, result['expanded'], spent[0] * 1000))
    assert len(spent) == 1 and spent[0] < 0.05 * total
