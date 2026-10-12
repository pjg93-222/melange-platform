"""Background route searches: cancel, supersede, abandonment and partial routes.

Searches are real AdvocacyRouter searches on tiny graphs. GateRouter holds a
search open the way a long one is held open, and still honours cancellation.
"""
import http.client
from http.server import ThreadingHTTPServer
import json
import random
import threading
import time
from types import SimpleNamespace

import pytest

from fec.surfaces import staffer_closeness_routes as closeness
from fec.surfaces.staffer_live_server import Handler
from fec.surfaces.staffer_live_service import (LiveService, ROUTE_ABANDON_SECONDS, ROUTE_ANONYMOUS_HOLDER,
                                               ROUTE_HOLDER_MAX_CHARS, route_holder)
from fec.surfaces.staffer_route_cancel import RouteCancelled, cancel_scope, check_cancelled
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.tests.surfaces.test_staffer_work_time_routes import person, episode

# Three contact-disjoint two-step routes from a to d.
DISJOINT = [('a', 'b'), ('b', 'd'), ('a', 'c'), ('c', 'd'), ('a', 'e'), ('e', 'd')]
TIMING = {'elapsed_ms', 'search_elapsed_ms', 'cache_elapsed_ms', 'original_search_elapsed_ms', 'search_id'}


def build_router(edges, router_class=None):
    nodes = {p: person('Person ' + p) for edge in edges for p in edge}
    episodes = [episode(p, 'office:' + a + b, '2020-01-01', '2021-01-01') for a, b in edges for p in (a, b)]
    return (router_class or AdvocacyRouter)(nodes, episodes, 'fixture', '2026-09-10')


class GateRouter(AdvocacyRouter):
    """route_many call number n waits for the gate when n is in block, polling for cancellation."""
    block = {1}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate = threading.Event()
        self.entered = {}
        self.calls = []
        self._calls_lock = threading.Lock()

    def entry(self, number):
        with self._calls_lock:
            return self.entered.setdefault(number, threading.Event())

    def route_many(self, origin, targets, options=None, progress=None):
        with self._calls_lock:
            self.calls.append((origin, tuple(targets)))
            number = len(self.calls)
        self.entry(number).set()
        if number in self.block:
            while not self.gate.wait(0.005):
                check_cancelled()
        return super().route_many(origin, targets, options, progress)


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


def normalized(value):
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items() if key not in TIMING}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


@pytest.fixture
def services():
    made = []

    def make(router):
        service = LiveService('fixture', SimpleNamespace(nodes={}))
        service.ready, service.stage = True, 'Ready'
        service.router, service.revision = router, router.revision
        made.append((service, router))
        return service
    yield make
    for service, router in made:
        with service.lock:
            for key in list(service.route_jobs):
                service._cancel_route_job(key, 'teardown')  # whoever still holds a search, it ends with the test
        getattr(router, 'gate', threading.Event()).set()
        service.route_executor.shutdown(wait=True, cancel_futures=True)


def job_of(service, started):
    return service.route_jobs[started['search_id']]


# ---------------------------------------------------------------- the helper

def test_check_cancelled_does_nothing_outside_a_scope_and_scopes_do_not_leak():
    check_cancelled()
    event = threading.Event()
    with cancel_scope(event):
        check_cancelled()
        event.set()
        with pytest.raises(RouteCancelled):
            check_cancelled()
    check_cancelled()  # the scope ended with the event still set
    with pytest.raises(RuntimeError):
        with cancel_scope(event):
            raise RuntimeError('a failing search still leaves the scope')
    check_cancelled()


def test_a_cancelled_event_is_seen_only_by_the_thread_running_inside_its_scope():
    event, outcome = threading.Event(), {}
    event.set()

    def other_thread():
        try:
            check_cancelled()
            outcome['other'] = 'unaffected'
        except RouteCancelled:
            outcome['other'] = 'cancelled'
    with cancel_scope(event):
        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join(2)
        with pytest.raises(RouteCancelled):
            check_cancelled()
    assert outcome == {'other': 'unaffected'}


# ------------------------------------------- searches stop when cancelled

def counting_neighbors(router, cancel_at):
    """Count _neighbors calls (one per expansion) and set the event on call cancel_at."""
    event, state, original = threading.Event(), {'calls': 0, 'after': 0}, router._neighbors

    def counted(*args, **kwargs):
        state['calls'] += 1
        if event.is_set():
            state['after'] += 1
        if state['calls'] == cancel_at:
            event.set()
        return original(*args, **kwargs)
    router._neighbors = counted
    return event, state


@pytest.mark.parametrize('objective', ['evidence_cost', 'fewest_steps', 'overlap_balanced'])
def test_a_search_stops_within_64_expansions_of_the_cancel(objective):
    chain = [(str(i), str(i + 1)) for i in range(700)]
    control = build_router(chain)
    event, control_state = counting_neighbors(control, 10 ** 9)
    assert control.route_many('0', ['700'], {'objective': objective})['status'] == 'found'
    assert control_state['calls'] > 300  # long enough that stopping early is visible

    router = build_router(chain)
    event, state = counting_neighbors(router, 100)
    with cancel_scope(event):
        with pytest.raises(RouteCancelled):
            router.route_many('0', ['700'], {'objective': objective})
    assert 0 < state['after'] <= 64
    assert state['calls'] < control_state['calls']


class FireAfterFirstSearch(AdvocacyRouter):
    """Sets the event once fire_after single-route searches have returned."""
    fire_after = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.event, self.starts, self.singles = threading.Event(), 0, 0

    def route_many(self, origin, targets, options=None, progress=None):
        self.starts += 1
        result = super().route_many(origin, targets, options, progress)
        if (options or {}).get('route_count') == 1:
            self.singles += 1
            if self.singles == self.fire_after:
                self.event.set()
        return result


@pytest.mark.parametrize('strategy,objective', [('distinct', 'evidence_cost'), ('ranked', 'evidence_cost'),
                                                ('distinct', 'work_time')])
def test_alternative_searches_check_between_iterations_and_before_spur_searches(strategy, objective):
    router = build_router(DISJOINT, FireAfterFirstSearch)
    with cancel_scope(router.event):
        with pytest.raises(RouteCancelled):
            router.route('a', 'd', {'objective': objective, 'route_count': 3, 'alternative_strategy': strategy})
    # The outer call and the first single search ran; no second or spur search began.
    assert router.starts == 2


def test_ranked_alternatives_check_before_every_spur_search():
    router = build_router(DISJOINT, FireAfterFirstSearch)
    router.fire_after = 2  # the cancel arrives while the first spur search is running
    with cancel_scope(router.event):
        with pytest.raises(RouteCancelled):
            router.route('a', 'd', {'objective': 'evidence_cost', 'route_count': 3, 'alternative_strategy': 'ranked'})
    # outer call, first search, first spur search; the second spur search of that round never began
    assert router.starts == 3


def test_a_job_cancelled_before_its_worker_picks_it_up_never_calls_the_search(services):
    service = services(build_router(DISJOINT))
    event, called = threading.Event(), []
    event.set()
    job = {'cancel': event, 'partial': ['kept until the job ends']}
    with pytest.raises(RouteCancelled):
        service._run_route_job(job, lambda *args: called.append(args), 'a', 'd', {}, None)
    assert called == [] and job['partial'] == []
    check_cancelled()  # and the scope was left


def test_uncancelled_alternative_searches_still_return_every_route():
    result = build_router(DISJOINT, FireAfterFirstSearch).route(
        'a', 'd', {'objective': 'evidence_cost', 'route_count': 3})
    assert result['routes_returned'] == 3


# --------------------------------------------- jobs: cancel, queued, cache

def test_cancel_ends_a_running_job_as_cancelled_frees_its_worker_and_is_not_reused(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = {1, 2}  # the first search, and the recomputation after the cancel
    service = services(router)
    options = {'objective': 'evidence_cost'}
    first = service.start_route_search('a', 'd', options)
    cancelled_job = job_of(service, first)
    assert router.entry(1).wait(5)
    assert service.route_search_result(first['search_id'])['status'] == 'searching'

    answer = service.cancel_route_search(first['search_id'])
    assert answer == {'ready': True, 'search_id': first['search_id'], 'status': 'cancelled'}
    polled = service.route_search_result(first['search_id'])
    assert polled['status'] == 'cancelled' and polled['cancel_reason'] == 'requested'
    assert polled['status'] not in ('unreachable', 'search_error')
    assert polled.get('found') is None and 'routes' not in polled
    # The worker is released: the search raised out of its loop.
    assert isinstance(cancelled_job['future'].exception(timeout=5), RouteCancelled)

    again = service.start_route_search('a', 'd', options)  # the same request computes again, from scratch
    assert again['search_id'] == first['search_id']
    assert again['status'] in ('queued', 'searching') and 'cache_hit' not in again
    assert service.route_jobs[again['search_id']] is not cancelled_job
    assert router.entry(2).wait(5)
    router.gate.set()
    assert job_of(service, again)['future'].result(timeout=5)['status'] == 'found'
    final = service.route_search_result(again['search_id'])
    assert final['status'] == 'found' and final['optimal']
    assert len(router.calls) == 2  # the cancelled search was not served from a cache


def test_the_second_request_after_a_cancel_is_a_new_job_not_the_cancelled_one(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = {1, 2}
    service = services(router)
    first = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    cancelled_job = job_of(service, first)
    assert router.entry(1).wait(5)
    service.cancel_route_search(first['search_id'])
    second = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    assert service.route_jobs[second['search_id']] is not cancelled_job
    assert cancelled_job['cancelled'] == 'requested'
    assert not service.route_jobs[second['search_id']]['cancelled']
    assert second['status'] in ('queued', 'searching')


def test_cancelling_a_busy_service_frees_a_slot_for_the_queued_search(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = {1, 2, 3}
    service = services(router)
    one = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    two = service.start_route_search('a', 'c', {'objective': 'evidence_cost'})
    assert router.entry(1).wait(5) and router.entry(2).wait(5)
    three = service.start_route_search('a', 'b', {'objective': 'evidence_cost'})
    assert three['status'] == 'queued'
    assert not router.entry(3).wait(0.1)  # both workers are held
    service.cancel_route_search(one['search_id'])
    assert router.entry(3).wait(5)  # the cancelled search released its worker
    assert service.route_search_result(three['search_id'])['status'] == 'searching'
    assert service.route_search_result(two['search_id'])['status'] == 'searching'
    router.gate.set()
    assert job_of(service, three)['future'].result(timeout=5)['status'] == 'found'


def test_a_search_cancelled_while_queued_never_starts(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = {1, 2}
    service = services(router)
    one = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    two = service.start_route_search('a', 'c', {'objective': 'evidence_cost'})
    assert router.entry(1).wait(5) and router.entry(2).wait(5)
    queued = service.start_route_search('a', 'b', {'objective': 'evidence_cost'})
    assert queued['status'] == 'queued'
    assert service.cancel_route_search(queued['search_id'])['status'] == 'cancelled'
    polled = service.route_search_result(queued['search_id'])
    assert polled['status'] == 'cancelled' and polled['cancel_reason'] == 'requested'
    assert job_of(service, queued)['future'].cancelled()
    router.gate.set()
    for started in (one, two):
        assert job_of(service, started)['future'].result(timeout=5)['status'] == 'found'
    time.sleep(0.05)
    assert ('a', ('b',)) not in router.calls  # its search never ran


def test_cancel_leaves_finished_and_unknown_searches_alone(services):
    service = services(build_router(DISJOINT))
    started = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    assert job_of(service, started)['future'].result(timeout=5)['status'] == 'found'
    assert service.cancel_route_search(started['search_id'])['status'] == 'finished'
    kept = service.route_search_result(started['search_id'])
    assert kept['status'] == 'found' and kept['optimal']
    repeat = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    assert repeat['cache_hit'] is True
    assert service.cancel_route_search('missing') == {'ready': True, 'search_id': 'missing', 'status': 'search_not_found'}
    assert service.cancel_route_search(started['search_id'])['status'] == 'finished'


def test_a_failing_search_is_still_a_search_error_not_a_cancel(services):
    service = services(build_router(DISJOINT))

    def broken(*args):
        raise ValueError('index unavailable')
    service.route = broken
    started = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    job_of(service, started)['future'].exception(timeout=5)
    polled = service.route_search_result(started['search_id'])
    assert polled['status'] == 'search_error' and 'index unavailable' in polled['error']


# ------------------------------------- the near-tie guard of the work_time search
#
# _search_bidirectional raises RuntimeError when its exact pass loses the cheapest route the float pass met (the exact optimum
# and the float best then differ by more than 1e-10 relative). That is not expected to happen; if it does, a job must end as
# search_error with that message, like any failing search: not as a cancel, not as a worker that never comes back, not as a
# search that never finishes. The tests force it the way the search's own test does: the tube the exact pass solves over is
# replaced by an empty one (_tube is looked up when the search runs).

def force_the_guard(monkeypatch):
    monkeypatch.setattr(closeness, '_tube', lambda *args: closeness._Tube(set(), set(), {}))


GUARD_STRATEGIES = [pytest.param({}, id='one route'),
                    pytest.param({'route_count': 3, 'alternative_strategy': 'ranked'}, id='ranked'),
                    pytest.param({'route_count': 3, 'alternative_strategy': 'distinct'}, id='distinct'),
                    pytest.param({'route_count': 3, 'alternative_strategy': 'independent'}, id='independent')]


@pytest.mark.parametrize('extra', GUARD_STRATEGIES)
def test_a_lost_cheapest_route_ends_the_job_as_a_search_error_and_frees_the_workers(services, monkeypatch, extra):
    options = {'objective': 'work_time', **extra}
    control = services(build_router(DISJOINT))      # without the guard forced, the same search finds its routes
    healthy = control.start_route_search('a', 'd', options)
    assert job_of(control, healthy)['future'].result(timeout=5)['status'] == 'found'

    service = services(build_router(DISJOINT))
    force_the_guard(monkeypatch)
    # more searches than the service has workers, so each one has to release its worker by raising out of it
    assert service.route_executor._max_workers == 2
    started = [service.start_route_search('a', target, options) for target in 'bcd']
    for one in started:
        failure = job_of(service, one)['future'].exception(timeout=5)      # it finished: not hung, not left searching
        assert isinstance(failure, RuntimeError) and 'lost the cheapest route' in str(failure)
        for _ in range(2):                                                 # every poll tells the same story
            polled = service.route_search_result(one['search_id'])
            assert polled['status'] == 'search_error' and polled['search_id'] == one['search_id']
            assert 'lost the cheapest route' in polled['error']
            assert not job_of(service, one)['cancelled'] and 'routes' not in polled and 'found' not in polled
    # the workers survived: with the guard no longer forced, the same service answers new searches
    monkeypatch.undo()
    later = [service.start_route_search('e', target, options) for target in 'abc']
    for one in later:
        assert job_of(service, one)['future'].result(timeout=5)['status'] == 'found'
        assert service.route_search_result(one['search_id'])['status'] == 'found'


def office_router():
    nodes = {p: person(p.upper()) for p in 'abc'}
    router = AdvocacyRouter(nodes, [episode(p, 'office:x', '2020-01-01', '2021-01-01') for p in nodes], 'fixture', '2026-09-10')
    router.office_index = {'office:x': {'id': 'office:x', 'label': 'Office X', 'records_through': '2021-01-01',
        'roster': {p: {'title': 'Title ' + p, 'basis': 'current_profile'} for p in 'bc'}}}
    return router


def test_a_lost_cheapest_route_in_an_office_search_is_a_search_error_too(services, monkeypatch):
    options = {'objective': 'work_time', 'route_count': 3, 'alternative_strategy': 'ranked'}
    control = services(office_router())
    healthy = control.start_route_search('a', 'office:x', options, office=True)
    assert job_of(control, healthy)['future'].result(timeout=5)['status'] == 'found'

    service = services(office_router())
    force_the_guard(monkeypatch)
    started = service.start_route_search('a', 'office:x', options, office=True)
    assert isinstance(job_of(service, started)['future'].exception(timeout=5), RuntimeError)
    polled = service.route_search_result(started['search_id'])
    assert polled['status'] == 'search_error' and 'lost the cheapest route' in polled['error'] and 'routes' not in polled


def test_a_lost_cheapest_route_reaches_the_page_as_a_search_error_over_http(services, served, monkeypatch):
    service = services(build_router(DISJOINT))
    http_server = served(service)
    force_the_guard(monkeypatch)
    status, started = http_server.request('GET', '/api/staffer-route?origin=a&target=d&objective=work_time&async=1&holder=page-a')
    # The forced guard raises as soon as a worker picks the search up, so that can happen before this first answer is built: it is queued, searching
    # or already the error. What is pinned is where the search ends, and that is read by polling until it is no longer queued or searching.
    assert status == 200 and started['status'] in ('queued', 'searching', 'search_error') and started['search_id']
    assert isinstance(job_of(service, started)['future'].exception(timeout=5), RuntimeError)
    # what the page polls: a plain answer with the status it already handles (it shows the error text), not a dropped connection
    seen = {}

    def finished():
        seen['status'], seen['answer'] = http_server.request('GET', '/api/staffer-route-result?id=' + started['search_id'])
        return seen['status'] == 200 and seen['answer']['status'] not in ('queued', 'searching')
    assert wait_until(finished, 5), seen
    status, polled = seen['status'], seen['answer']
    assert status == 200 and polled['status'] == 'search_error' and polled['search_id'] == started['search_id']
    assert 'lost the cheapest route' in polled['error']
    # the server and its workers are still up
    monkeypatch.undo()
    status, next_one = http_server.request('GET', '/api/staffer-route?origin=e&target=a&objective=work_time&async=1')
    assert status == 200
    assert job_of(service, next_one)['future'].result(timeout=5)['status'] == 'found'
    status, finished = http_server.request('GET', '/api/staffer-route-result?id=' + next_one['search_id'])
    assert status == 200 and finished['status'] == 'found' and finished['optimal']


# ------------------------------------------------------------- supersede

def test_a_new_search_can_supersede_the_previous_one(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = {1, 2}
    service = services(router)
    old = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    assert router.entry(1).wait(5)
    new = service.start_route_search('a', 'c', {'objective': 'evidence_cost'}, supersedes=old['search_id'])
    assert new['search_id'] != old['search_id']
    polled = service.route_search_result(old['search_id'])
    assert polled['status'] == 'cancelled' and polled['cancel_reason'] == 'superseded'
    assert isinstance(job_of(service, old)['future'].exception(timeout=5), RouteCancelled)
    assert service.route_search_result(new['search_id'])['status'] in ('queued', 'searching')
    router.gate.set()
    assert job_of(service, new)['future'].result(timeout=5)['status'] == 'found'


def test_superseding_with_the_same_request_keeps_the_running_search(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    first = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    running = job_of(service, first)
    assert router.entry(1).wait(5)
    again = service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, supersedes=first['search_id'])
    assert again['search_id'] == first['search_id'] and again['status'] == 'searching'
    assert job_of(service, again) is running and not running['cancelled']  # the very same job, still running
    assert not router.entry(2).wait(0.05) and len(router.calls) == 1  # nothing was restarted


def test_superseding_an_unknown_or_finished_search_changes_nothing(services):
    service = services(build_router(DISJOINT))
    done = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    job_of(service, done)['future'].result(timeout=5)
    other = service.start_route_search('a', 'c', {'objective': 'evidence_cost'},
                                       supersedes=done['search_id'])
    assert not job_of(service, done)['cancelled']
    service.start_route_search('a', 'b', {'objective': 'evidence_cost'}, supersedes='no-such-search')
    assert service.route_search_result(done['search_id'])['status'] == 'found'
    assert job_of(service, other)['future'].result(timeout=5)['status'] == 'found'


def test_a_loading_service_still_applies_the_supersede(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    old = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    assert router.entry(1).wait(5)
    service._assertion_update_pending = True
    try:
        answer = service.start_route_search('a', 'c', {'objective': 'evidence_cost'}, supersedes=old['search_id'])
    finally:
        service._assertion_update_pending = False
    assert answer['status'] == 'loading'
    assert service.route_search_result(old['search_id'])['cancel_reason'] == 'superseded'


# ---------------------------------------- one search, several pages (holders)
#
# Identical requests share one job, so a cancel, a supersede or a closing page in one tab must drop only that tab's
# claim. Each negative control runs the pre-holder behaviour (the job-level cancel) through the same sequence.

def legacy_cancel(service, key, reason='requested'):
    """What a cancel and a supersede did before holders: the search itself was cancelled, whichever page asked."""
    with service.lock:
        return service._cancel_route_job(key, reason)


def two_pages_on_one_search(service, router, holders=('page-a', 'page-b')):
    """Every page asks for the identical request, so all of them share one running search."""
    first = service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder=holders[0])
    assert router.entry(1).wait(5)
    joined = [service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder=holder) for holder in holders[1:]]
    assert all(answer['search_id'] == first['search_id'] for answer in joined), 'one shared search'
    assert job_of(service, first)['holders'] == set(holders)
    return first


def test_one_pages_cancel_leaves_another_pages_identical_search_running_and_that_page_gets_its_result(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    first = two_pages_on_one_search(service, router)
    job = job_of(service, first)
    answer = service.cancel_route_search(first['search_id'], holder='page-a')  # page A: Clear, or its tab closes
    assert answer == {'ready': True, 'search_id': first['search_id'], 'status': 'released'}
    assert not job['cancelled'] and not job['cancel'].is_set() and job['holders'] == {'page-b'}
    assert service.route_search_result(first['search_id'])['status'] == 'searching'  # page B's poll
    router.gate.set()
    assert job['future'].result(timeout=5)['status'] == 'found'
    final = service.route_search_result(first['search_id'])
    assert final['status'] == 'found' and final['optimal']
    assert len(router.calls) == 1, 'the search was never restarted'


def test_one_pages_supersede_leaves_another_pages_identical_search_running_and_that_page_gets_its_result(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    first = two_pages_on_one_search(service, router)
    job = job_of(service, first)
    moved_on = service.start_route_search('a', 'c', {'objective': 'evidence_cost'}, supersedes=first['search_id'],
                                          holder='page-a')  # page A searches something else
    assert moved_on['search_id'] != first['search_id']
    assert not job['cancelled'] and job['holders'] == {'page-b'}
    assert service.route_search_result(first['search_id'])['status'] == 'searching'
    router.gate.set()
    assert job['future'].result(timeout=5)['status'] == 'found'
    assert service.route_search_result(first['search_id'])['status'] == 'found'
    assert job_of(service, moved_on)['future'].result(timeout=5)['status'] == 'found', "and page A's new search finishes too"


def test_a_single_pages_cancel_still_stops_its_search_and_frees_the_worker(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = {1, 2}
    service = services(router)
    first = service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder='page-a')
    job = job_of(service, first)
    assert router.entry(1).wait(5)
    answer = service.cancel_route_search(first['search_id'], holder='page-a')
    assert answer == {'ready': True, 'search_id': first['search_id'], 'status': 'cancelled'}
    polled = service.route_search_result(first['search_id'])
    assert polled['status'] == 'cancelled' and polled['cancel_reason'] == 'requested'
    assert isinstance(job['future'].exception(timeout=5), RouteCancelled)
    again = service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder='page-a')
    assert service.route_jobs[again['search_id']] is not job and service.route_jobs[again['search_id']]['holders'] == {'page-a'}


def test_the_search_is_cancelled_when_its_last_holder_lets_go_and_not_before(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    first = two_pages_on_one_search(service, router, ('page-a', 'page-b', 'page-c'))
    job, key = job_of(service, first), first['search_id']
    assert service.cancel_route_search(key, holder='page-a')['status'] == 'released'
    assert service.cancel_route_search(key, holder='page-a')['status'] == 'released', 'letting go twice changes nothing'
    assert service.cancel_route_search(key, holder='stranger')['status'] == 'released', 'a page that never held it cannot stop it'
    assert job['holders'] == {'page-b', 'page-c'} and not job['cancelled']
    assert service.cancel_route_search(key, holder='page-b')['status'] == 'released'
    assert not job['cancelled'] and not job['cancel'].is_set()
    assert service.cancel_route_search(key, holder='page-c')['status'] == 'cancelled'  # the last one out
    assert job['cancelled'] == 'requested' and job['cancel'].is_set()
    assert isinstance(job['future'].exception(timeout=5), RouteCancelled)
    polled = service.route_search_result(key)
    assert polled['status'] == 'cancelled' and polled['cancel_reason'] == 'requested'
    assert service.cancel_route_search(key, holder='page-c')['status'] == 'cancelled', 'a cancelled search stays cancelled'


def test_the_last_holder_to_supersede_cancels_the_search_as_superseded(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = {1, 2}
    service = services(router)
    first = two_pages_on_one_search(service, router)
    job = job_of(service, first)
    service.start_route_search('a', 'c', {'objective': 'evidence_cost'}, supersedes=first['search_id'], holder='page-a')
    assert not job['cancelled']
    service.start_route_search('a', 'b', {'objective': 'evidence_cost'}, supersedes=first['search_id'], holder='page-b')
    assert job['cancelled'] == 'superseded'
    assert service.route_search_result(first['search_id'])['cancel_reason'] == 'superseded'


def test_a_request_from_the_same_page_again_adds_no_holder_and_one_cancel_lets_go_of_it(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    first = service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder='page-a')
    assert router.entry(1).wait(5)
    again = service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, supersedes=first['search_id'], holder='page-a')
    assert again['search_id'] == first['search_id'] and job_of(service, first)['holders'] == {'page-a'}
    assert not job_of(service, first)['cancelled'], 'an identical supersede keeps its own search'
    assert service.cancel_route_search(first['search_id'], holder='page-a')['status'] == 'cancelled'


def test_a_finished_search_keeps_its_result_for_every_holder(services):
    service = services(build_router(DISJOINT))
    first = service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder='page-a')
    job_of(service, first)['future'].result(timeout=5)
    again = service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder='page-b')
    assert again['cache_hit'] is True and job_of(service, first)['holders'] == {'page-a', 'page-b'}
    assert service.cancel_route_search(first['search_id'], holder='page-a')['status'] == 'finished'
    assert service.cancel_route_search(first['search_id'], holder='page-b')['status'] == 'finished'
    assert service.route_search_result(first['search_id'])['status'] == 'found'


def test_requests_without_a_token_share_one_anonymous_holder_exactly_as_before(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    first = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    assert router.entry(1).wait(5)
    second = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    assert second['search_id'] == first['search_id'] and job_of(service, first)['holders'] == {ROUTE_ANONYMOUS_HOLDER}
    answer = service.cancel_route_search(first['search_id'])
    assert answer == {'ready': True, 'search_id': first['search_id'], 'status': 'cancelled'}, 'one tokenless cancel ends it for every tokenless request'
    assert service.route_search_result(first['search_id'])['cancel_reason'] == 'requested'


def test_a_cancel_without_a_token_never_stops_a_search_a_page_holds_under_its_own_token(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    first = service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder='page-a')
    assert router.entry(1).wait(5)
    service.start_route_search('a', 'd', {'objective': 'evidence_cost'})  # an older page or a script joins
    job, key = job_of(service, first), first['search_id']
    assert job['holders'] == {'page-a', ROUTE_ANONYMOUS_HOLDER}
    assert service.cancel_route_search(key)['status'] == 'released'
    assert not job['cancelled'] and job['holders'] == {'page-a'}
    assert service.route_search_result(key)['status'] == 'searching'
    service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    assert service.cancel_route_search(key, holder='page-a')['status'] == 'released', 'and a page leaves the tokenless holder alone'
    assert not job['cancelled'] and job['holders'] == {ROUTE_ANONYMOUS_HOLDER}
    assert service.cancel_route_search(key)['status'] == 'cancelled'


def test_a_holder_is_a_string_of_at_most_64_characters_and_a_bad_one_creates_nothing(services):
    assert route_holder(None) == ROUTE_ANONYMOUS_HOLDER == ''
    assert route_holder('page-a') == 'page-a'
    assert route_holder('z' * 100) == 'z' * ROUTE_HOLDER_MAX_CHARS and ROUTE_HOLDER_MAX_CHARS == 64
    for bad in (7, 1.5, ['a'], {'a': 1}, b'bytes', True):
        with pytest.raises(TypeError):
            route_holder(bad)
    service = services(build_router(DISJOINT))
    with pytest.raises(TypeError):
        service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder=7)
    with pytest.raises(TypeError):
        service.cancel_route_search('whatever', holder=7)
    assert service.route_jobs == {}


def test_the_sweeper_cancels_a_search_whoever_still_holds_it_and_a_poll_by_anyone_keeps_it_alive(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    service.route_reap_interval = 3600.0  # the test calls the sweep itself, at the instants it chooses
    now = [100.0]
    service.route_clock = lambda: now[0]
    first = two_pages_on_one_search(service, router)
    job, key = job_of(service, first), first['search_id']
    now[0] = 189.0
    assert service.reap_abandoned_route_jobs() == []
    assert service.route_search_result(key)['status'] == 'searching'  # page B asks: that is a poll, not a claim
    now[0] = 278.0
    assert service.reap_abandoned_route_jobs() == []
    now[0] = 279.0
    assert service.reap_abandoned_route_jobs() == [key]
    assert job['cancelled'] == 'abandoned' and job['holders'] == {'page-a', 'page-b'}, 'the sweeper works on the search, not on its holders'


def test_negative_control_the_pre_holder_cancel_stops_the_search_for_every_page(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    first = two_pages_on_one_search(service, router)
    legacy_cancel(service, first['search_id'])  # page A's cancel, as it acted before holders
    polled_by_b = service.route_search_result(first['search_id'])
    assert polled_by_b['status'] == 'cancelled' and polled_by_b['cancel_reason'] == 'requested', 'the harness must see the old defect'


def test_negative_control_the_pre_holder_supersede_cancels_the_search_for_every_page(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    first = two_pages_on_one_search(service, router)
    legacy_cancel(service, first['search_id'], 'superseded')  # page A moves on, as it acted before holders
    polled_by_b = service.route_search_result(first['search_id'])
    assert polled_by_b['status'] == 'cancelled' and polled_by_b['cancel_reason'] == 'superseded', 'the harness must see the old defect'


def test_a_page_that_keeps_waiting_never_loses_its_search_to_other_pages_cancelling_and_superseding(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = set(range(1, 100000))  # every search stays open; nothing here waits for a result
    service = services(router)
    keeper = service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder='keeper')
    key, job, problems = keeper['search_id'], job_of(service, keeper), []

    def page(number):
        rng, holder = random.Random(number), 'page-%d' % number
        try:
            for _ in range(150):
                action = rng.choice(('join', 'cancel', 'supersede'))
                if action == 'join':
                    assert service.start_route_search('a', 'd', {'objective': 'evidence_cost'}, holder=holder)['search_id'] == key
                elif action == 'cancel':
                    assert service.cancel_route_search(key, holder=holder)['status'] == 'released'
                else:
                    service.start_route_search('a', 'c', {'objective': 'evidence_cost', 'limit': 1000 + number},
                                               supersedes=key, holder=holder)
                assert not job['cancelled'] and 'keeper' in job['holders']
            assert service.cancel_route_search(key, holder=holder)['status'] == 'released'  # this page is done with it
        except BaseException as exc:  # noqa: BLE001 - reported on the main thread
            problems.append(repr(exc))
    pages = [threading.Thread(target=page, args=(number,), daemon=True) for number in range(8)]
    for thread in pages:
        thread.start()
    for thread in pages:
        thread.join(30)
    assert not any(thread.is_alive() for thread in pages) and problems == []
    assert not job['cancelled'] and job['holders'] == {'keeper'}
    assert service.route_search_result(key)['status'] in ('queued', 'searching')
    assert service.cancel_route_search(key, holder='keeper')['status'] == 'cancelled'  # the last one out


def test_the_holders_of_a_search_follow_a_model_of_joins_leaves_and_supersedes(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = set(range(1, 100000))
    service = services(router)
    rng = random.Random(20261003)
    pages = ['page-a', 'page-b', 'page-c', ROUTE_ANONYMOUS_HOLDER]
    elsewhere = {'objective': 'evidence_cost', 'limit': 9999}  # what a superseding page searches instead: one shared request
    cancels = 0
    for trial in range(50):  # fewer than 64 searches in all, so none is reclaimed while the model still tracks it
        options = {'objective': 'evidence_cost', 'limit': 100 + trial}  # one new shared request per trial
        key, held, cancelled, why = None, set(), False, None
        for _ in range(25):
            page, action = rng.choice(pages), rng.choice(('join', 'join', 'leave', 'supersede'))
            if action == 'join':
                answer = service.start_route_search('a', 'd', options, holder=page)
                if key is None or cancelled:
                    key, held, cancelled, why = answer['search_id'], set(), False, None  # a cancelled search is never reused
                assert answer['search_id'] == key
                held.add(page)
            elif key is not None:
                if action == 'leave':
                    status, reason = service.cancel_route_search(key, holder=page)['status'], 'requested'
                else:
                    service.start_route_search('a', 'c', elsewhere, supersedes=key, holder=page)
                    status, reason = None, 'superseded'
                if cancelled:
                    expected = 'cancelled'
                else:
                    held.discard(page)
                    expected = 'released' if held else 'cancelled'
                    if not held:
                        cancelled, why = True, reason
                        cancels += 1
                if status is not None:
                    assert status == expected, (trial, action, page)
            if key is not None:
                job = service.route_jobs[key]
                assert bool(job['cancelled']) == cancelled and job['cancel'].is_set() == cancelled
                assert job['cancelled'] == (why if cancelled else None)
                if not cancelled:
                    assert job['holders'] == held
    assert cancels > 20, 'the sequences must exercise the last-holder cancel often enough to mean something'


# ------------------------------------------------------------ abandonment

def test_a_search_nobody_polls_for_90_seconds_is_cancelled(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = {1, 2}
    service = services(router)
    now = [100.0]
    service.route_clock = lambda: now[0]
    watched = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    left = service.start_route_search('a', 'c', {'objective': 'evidence_cost'})
    assert router.entry(1).wait(5) and router.entry(2).wait(5)
    queued = service.start_route_search('a', 'b', {'objective': 'evidence_cost'})
    assert queued['status'] == 'queued'
    # Chrome slows a tab hidden for over five minutes to about one wake-up a minute, so 15 s would cancel
    # searches that are still wanted; closing the tab cancels at once (pagehide), this window is the fallback.
    assert ROUTE_ABANDON_SECONDS == 90.0

    now[0] = 189.0
    assert service.reap_abandoned_route_jobs() == []
    assert service.route_search_result(watched['search_id'])['status'] == 'searching'  # a poll keeps it alive
    now[0] = 190.0
    assert sorted(service.reap_abandoned_route_jobs()) == sorted([left['search_id'], queued['search_id']])
    assert job_of(service, left)['cancelled'] == 'abandoned'
    assert job_of(service, queued)['cancelled'] == 'abandoned'
    assert not job_of(service, watched)['cancelled']
    assert service.reap_abandoned_route_jobs() == []  # already cancelled searches are not counted twice
    polled = service.route_search_result(left['search_id'])
    assert polled['status'] == 'cancelled' and polled['cancel_reason'] == 'abandoned'

    now[0] = 278.0  # the watched search was last polled at 189.0
    assert service.reap_abandoned_route_jobs() == []
    now[0] = 279.0
    assert service.reap_abandoned_route_jobs() == [watched['search_id']]
    assert isinstance(job_of(service, watched)['future'].exception(timeout=5), RouteCancelled)


def test_repeating_a_request_counts_as_asking_about_the_search(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    now = [0.0]
    service.route_clock = lambda: now[0]
    first = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    assert router.entry(1).wait(5)
    now[0] = 89.0
    service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    now[0] = 178.0
    assert service.reap_abandoned_route_jobs() == []
    now[0] = 179.0
    assert service.reap_abandoned_route_jobs() == [first['search_id']]


def test_finished_searches_are_never_reaped(services):
    service = services(build_router(DISJOINT))
    now = [0.0]
    service.route_clock = lambda: now[0]
    started = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    job_of(service, started)['future'].result(timeout=5)
    now[0] = 10 ** 6
    assert service.reap_abandoned_route_jobs() == []
    assert service.route_search_result(started['search_id'])['status'] == 'found'


def test_the_sweeper_cancels_an_abandoned_search_with_no_request_and_then_stops(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    service.route_reap_interval = 0.01
    now = [0.0]
    service.route_clock = lambda: now[0]
    started = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    job = job_of(service, started)
    assert router.entry(1).wait(5)
    sweeper = service._route_reaper
    assert sweeper is not None and sweeper.is_alive()
    now[0] = ROUTE_ABANDON_SECONDS  # no one polls; reading the job directly does not count as a poll
    assert wait_until(lambda: job['cancelled'] == 'abandoned')
    assert isinstance(job['future'].exception(timeout=5), RouteCancelled)
    assert wait_until(lambda: service._route_reaper is None and not sweeper.is_alive())
    # The next search starts a fresh sweeper.
    router.block = {2}
    again = service.start_route_search('a', 'c', {'objective': 'evidence_cost'})
    assert service._route_reaper is not None and service._route_reaper is not sweeper
    assert router.entry(2).wait(5)
    router.gate.set()
    assert job_of(service, again)['future'].result(timeout=5)['status'] == 'found'


# ------------------------------------------------------- partial routes

@pytest.mark.parametrize('objective', ['evidence_cost', 'work_time'])
def test_partial_route_one_equals_final_route_one(services, objective):
    router = build_router(DISJOINT, GateRouter)
    router.block = {3}  # the second single search (outer call 1, searches 2 and 3)
    service = services(router)
    options = {'objective': objective, 'route_count': 3}
    started = service.start_route_search('a', 'd', options)
    assert started['status'] in ('queued', 'searching')
    assert router.entry(3).wait(5)

    polled = service.route_search_result(started['search_id'])
    assert polled['status'] == 'searching' and polled['routes_requested'] == 3
    assert polled['progress']['routes_found'] == 1
    assert len(polled['partial_routes']) == 1
    partial = polled['partial_routes'][0]
    assert partial['route_rank'] == 1 and partial['found'] is True and partial['optimal'] is True
    json.dumps(polled)  # what the server sends

    router.gate.set()
    final = job_of(service, started)['future'].result(timeout=10)
    assert len(final['routes']) == 3
    assert normalized(partial) == normalized(final['routes'][0])
    done = service.route_search_result(started['search_id'])
    assert 'partial_routes' not in done and 'routes_requested' in done and done['search_id'] == started['search_id']


def test_each_accepted_route_is_published_in_rank_order(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = {4}  # the third single search
    service = services(router)
    started = service.start_route_search('a', 'd', {'objective': 'evidence_cost', 'route_count': 3})
    assert router.entry(4).wait(5)
    polled = service.route_search_result(started['search_id'])
    assert [r['route_rank'] for r in polled['partial_routes']] == [1, 2]
    assert polled['progress']['routes_found'] == 2
    router.gate.set()
    final = job_of(service, started)['future'].result(timeout=10)
    assert [normalized(r) for r in polled['partial_routes']] == [normalized(r) for r in final['routes'][:2]]


def test_partial_office_routes_carry_the_office_fields_of_the_final_routes(services):
    router = build_router(DISJOINT, GateRouter)
    router.block = {3}
    router.office_index = {'office:x': {'id': 'office:x', 'label': 'Office X', 'records_through': '2026-03-31',
        'roster': {p: {'title': 'Staff Assistant ' + p, 'basis': 'current_profile'} for p in 'bce'}}}
    service = services(router)
    started = service.start_route_search('a', 'office:x', {'objective': 'evidence_cost', 'route_count': 3}, office=True)
    assert router.entry(3).wait(5)
    polled = service.route_search_result(started['search_id'])
    partial = polled['partial_routes'][0]
    assert partial['office']['label'] == 'Office X' and partial['candidate_count'] == 3
    assert partial['recommended_contact']['title'] == 'Staff Assistant ' + partial['target']
    router.gate.set()
    final = job_of(service, started)['future'].result(timeout=10)
    assert len(final['routes']) == 3
    assert normalized(partial) == normalized(final['routes'][0])


def test_a_single_route_search_reports_no_partial_routes(services):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    started = service.start_route_search('a', 'd', {'objective': 'evidence_cost'})
    assert router.entry(1).wait(5)
    polled = service.route_search_result(started['search_id'])
    assert polled['status'] == 'searching' and polled['routes_requested'] == 1 and 'partial_routes' not in polled
    router.gate.set()


@pytest.mark.parametrize('objective', ['evidence_cost', 'work_time', 'fewest_steps'])
def test_the_finished_payload_equals_the_synchronous_path(services, objective):
    options = {'objective': objective, 'route_count': 3}
    expected = services(build_router(DISJOINT)).route('a', 'd', options)  # no progress callback: the unchanged path
    job_service = services(build_router(DISJOINT))
    started = job_service.start_route_search('a', 'd', options)
    job_of(job_service, started)['future'].result(timeout=10)
    final = job_service.route_search_result(started['search_id'])
    assert final['search_id'] == started['search_id']
    assert normalized(final) == normalized(expected)
    assert 'partial_routes' not in final and len(final['routes']) == 3


def test_a_caller_supplied_progress_callback_gets_shaped_partial_routes_and_the_same_result(services):
    options = {'objective': 'evidence_cost', 'route_count': 3}
    result = services(build_router(DISJOINT)).route('a', 'd', options)  # no callback
    seen = []
    again = services(build_router(DISJOINT)).route('a', 'd', options, seen.append)
    assert [value['routes_found'] for value in seen if 'routes_found' in value] == [1, 2, 3]
    assert all('accepted_route' not in value for value in seen)
    partials = [value['partial_route'] for value in seen if 'partial_route' in value]
    assert [route['route_rank'] for route in partials] == [1, 2, 3]
    assert all(route['found'] and route['hops'] and route['graph']['nodes'] for route in partials)
    assert normalized(again) == normalized(result)


# ------------------------------------------------------------------ HTTP

class FakeRouteService:
    def __init__(self):
        self.calls = []

    def start_route_search(self, origin, target, options=None, office=False, **extra):
        self.calls.append(('start', origin, target, office, extra))
        return {'ready': True, 'status': 'queued', 'search_id': 'fresh'}

    def cancel_route_search(self, key, **extra):
        self.calls.append(('cancel', key, extra) if extra else ('cancel', key))
        return {'ready': True, 'search_id': key, 'status': 'cancelled'}


class OldService:
    """A service that predates cancellation: start_route_search takes no supersedes."""
    calls = []

    def start_route_search(self, origin, target, options=None, office=False):
        self.calls.append((origin, target))
        return {'ready': True, 'status': 'queued', 'search_id': 'old'}


class Served:
    def __init__(self, service):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.dashboard = service
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, payload=None, headers=None, body=None):
        if body is None and payload is not None:
            body = json.dumps(payload).encode()
        sent = {'Origin': f'http://127.0.0.1:{self.port}'}
        if method == 'POST':
            sent['Content-Type'] = 'application/json'
        sent.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None
        finally:
            connection.close()


@pytest.fixture
def served():
    opened = []

    def serve(service):
        opened.append(Served(service))
        return opened[-1]
    yield serve
    for item in opened:
        item.close()


def test_cancel_endpoint_dispatches_the_search_id(served):
    service = FakeRouteService()
    http_server = served(service)
    status, answer = http_server.request('POST', '/api/staffer-route-cancel', {'id': 'abc123'})
    assert status == 200 and answer == {'ready': True, 'search_id': 'abc123', 'status': 'cancelled'}
    assert service.calls == [('cancel', 'abc123')]


def test_cancel_endpoint_uses_the_local_origin_gate_and_validates_the_id(served):
    service = FakeRouteService()
    http_server = served(service)
    path = '/api/staffer-route-cancel'
    # The header guards answer before the body is read. These send no body: on Windows a socket closed
    # with unread bytes is reset and hides the answer (the handler's 404 branch drains for the same reason).
    refused = [{'Origin': 'https://unrelated.example'}, {'Origin': 'http://127.0.0.1:1'},
               {'Origin': 'https://127.0.0.1:' + str(http_server.port)}, {'Sec-Fetch-Site': 'cross-site'},
               {'Content-Type': 'text/plain'}, {'Content-Length': '16385'}]
    answers = []
    for headers in refused:
        status, answer = http_server.request('POST', path, headers=headers, body=b'')
        assert status == 400 and 'error' in answer, headers
        answers.append(answer['error'])
    status, answer = http_server.request('POST', path, body=b'')  # an empty body is not a search id
    assert status == 400 and 'error' in answer
    # Payload checks read the whole body first.
    for payload in ({}, {'id': 7}, {'id': ''}, {'id': 'x' * 65}, {'id': ['abc']}):
        status, answer = http_server.request('POST', path, payload)
        assert status == 400 and answer['error'] == 'Choose a search to cancel', payload
    for body in (b'{broken', b'[]', b'null'):
        status, answer = http_server.request('POST', path, body=body)
        assert status == 400, body
    assert service.calls == []
    assert all('route search' in text.lower() and 'identity' not in text.lower() for text in answers)


def test_cancel_endpoint_reports_a_service_without_cancellation(served):
    status, answer = served(OldService()).request('POST', '/api/staffer-route-cancel', {'id': 'abc'})
    assert status == 503 and 'unavailable' in answer['error']


def test_the_post_routes_stay_closed_to_other_paths(served):
    service = FakeRouteService()
    http_server = served(service)
    for path in ('/api/staffer-route', '/api/staffer-route-result', '/api/staffer-route-cancel/', '/api/staffer-route-cancel2'):
        connection = http.client.HTTPConnection('127.0.0.1', http_server.port, timeout=5)
        try:
            connection.request('POST', path, body=b'{}', headers={'Content-Type': 'application/json'})
            assert connection.getresponse().status == 404, path
        finally:
            connection.close()
    assert service.calls == []


def test_supersedes_is_passed_only_when_the_request_carries_it(served):
    service = FakeRouteService()
    http_server = served(service)
    http_server.request('GET', '/api/staffer-route?origin=a&target=d&async=1')
    http_server.request('GET', '/api/staffer-route?origin=a&target=d&async=1&supersedes=old-search')
    http_server.request('GET', '/api/staffer-office-route?origin=a&office=office%3Ax&async=1&supersedes=older')
    http_server.request('GET', '/api/staffer-route?origin=a&target=d&async=1&supersedes=' + 'z' * 100)
    assert service.calls == [('start', 'a', 'd', False, {}),
                             ('start', 'a', 'd', False, {'supersedes': 'old-search'}),
                             ('start', 'a', 'office:x', True, {'supersedes': 'older'}),
                             ('start', 'a', 'd', False, {'supersedes': 'z' * 64})]


def test_the_holder_token_is_passed_on_only_when_the_request_carries_it(served):
    service = FakeRouteService()
    http_server = served(service)
    http_server.request('GET', '/api/staffer-route?origin=a&target=d&async=1&holder=page-a')
    http_server.request('GET', '/api/staffer-office-route?origin=a&office=office%3Ax&async=1&holder=page-b&supersedes=older')
    http_server.request('GET', '/api/staffer-route?origin=a&target=d&async=1&holder=' + 'h' * 100)
    http_server.request('GET', '/api/staffer-route?origin=a&target=d&async=1&holder=')  # an empty token is no token
    assert service.calls == [('start', 'a', 'd', False, {'holder': 'page-a'}),
                             ('start', 'a', 'office:x', True, {'holder': 'page-b', 'supersedes': 'older'}),
                             ('start', 'a', 'd', False, {'holder': 'h' * 64}),
                             ('start', 'a', 'd', False, {})]
    service.calls.clear()
    status, answer = http_server.request('POST', '/api/staffer-route-cancel', {'id': 'abc123', 'holder': 'page-a'})
    assert status == 200 and answer['status'] == 'cancelled'
    for payload in ({'id': 'abc123'}, {'id': 'abc123', 'holder': None}):  # no token: the shared anonymous holder
        assert http_server.request('POST', '/api/staffer-route-cancel', payload)[0] == 200
    assert service.calls == [('cancel', 'abc123', {'holder': 'page-a'}), ('cancel', 'abc123'), ('cancel', 'abc123')]


def test_a_cancel_with_a_bad_holder_is_refused_before_it_reaches_the_service(served):
    service = FakeRouteService()
    http_server = served(service)
    for holder in (7, '', 'x' * 65, ['page-a'], {'id': 'page-a'}, True):
        status, answer = http_server.request('POST', '/api/staffer-route-cancel', {'id': 'abc123', 'holder': holder})
        assert status == 400 and answer['error'] == 'Invalid search holder', holder
    assert service.calls == []


@pytest.mark.parametrize('site,honoured', [(None, True), ('same-origin', True), ('none', True), ('Same-Origin', True),
                                           ('cross-site', False), ('same-site', False), ('bogus', False)])
def test_a_get_supersede_is_honoured_only_for_a_same_origin_or_typed_in_request(served, site, honoured):
    service = FakeRouteService()
    http_server = served(service)
    headers = {'Sec-Fetch-Site': site} if site else {}
    status, answer = http_server.request('GET', '/api/staffer-route?origin=a&target=d&async=1&holder=page-a&supersedes=old-search',
                                         headers=headers)
    assert status == 200 and answer['search_id'] == 'fresh', 'the search itself still starts'
    expected = {'holder': 'page-a', 'supersedes': 'old-search'} if honoured else {'holder': 'page-a'}
    assert service.calls == [('start', 'a', 'd', False, expected)]
    service.calls.clear()
    http_server.request('GET', '/api/staffer-office-route?origin=a&office=office%3Ax&async=1&supersedes=older', headers=headers)
    assert service.calls == [('start', 'a', 'office:x', True, {'supersedes': 'older'} if honoured else {})]


def test_a_cross_site_get_cannot_cancel_a_running_search_through_supersedes(services, served):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    http_server = served(service)
    query = '/api/staffer-route?origin=a&target=d&objective=evidence_cost&async=1&holder=page-a'
    status, started = http_server.request('GET', query)
    assert status == 200 and router.entry(1).wait(5)
    other = query.replace('target=d', 'target=c') + '&supersedes=' + started['search_id']
    for site in ('cross-site', 'same-site'):
        status, answer = http_server.request('GET', other, headers={'Sec-Fetch-Site': site})
        assert status == 200 and answer['search_id'] != started['search_id']
        assert not job_of(service, started)['cancelled'], site  # the stranger's supersede changed nothing
        assert job_of(service, started)['holders'] == {'page-a'}
    status, answer = http_server.request('GET', other, headers={'Sec-Fetch-Site': 'same-origin'})
    assert status == 200 and job_of(service, started)['cancelled'] == 'superseded'  # the page's own supersede still works


def test_two_pages_over_http_share_one_search_and_one_pages_cancel_leaves_the_other_its_result(services, served):
    router = build_router(DISJOINT, GateRouter)
    service = services(router)
    http_server = served(service)
    query = '/api/staffer-route?origin=a&target=d&objective=evidence_cost&async=1&holder='
    status, first = http_server.request('GET', query + 'page-a')
    assert status == 200 and router.entry(1).wait(5)
    status, second = http_server.request('GET', query + 'page-b')
    assert status == 200 and second['search_id'] == first['search_id']
    status, answer = http_server.request('POST', '/api/staffer-route-cancel', {'id': first['search_id'], 'holder': 'page-a'})
    assert status == 200 and answer['status'] == 'released'
    status, polled = http_server.request('GET', '/api/staffer-route-result?id=' + first['search_id'])
    assert polled['status'] == 'searching'
    router.gate.set()
    assert job_of(service, first)['future'].result(timeout=5)['status'] == 'found'
    status, finished = http_server.request('GET', '/api/staffer-route-result?id=' + first['search_id'])
    assert finished['status'] == 'found' and finished['optimal']
    status, answer = http_server.request('POST', '/api/staffer-route-cancel', {'id': first['search_id'], 'holder': 'page-b'})
    assert answer['status'] == 'finished'


def test_a_service_without_supersedes_still_answers_plain_async_requests(served):
    http_server = served(OldService())
    status, answer = http_server.request('GET', '/api/staffer-route?origin=a&target=d&async=1')
    assert status == 200 and answer['search_id'] == 'old'


def test_http_round_trip_search_partial_routes_cancel_and_supersede(services, served):
    router = build_router(DISJOINT, GateRouter)
    router.block = {3}
    service = services(router)
    http_server = served(service)
    query = '/api/staffer-route?origin=a&target=d&objective=evidence_cost&route_count=3&async=1'
    status, started = http_server.request('GET', query)
    assert status == 200 and started['status'] in ('queued', 'searching') and started['routes_requested'] == 3
    assert router.entry(3).wait(5)
    status, polled = http_server.request('GET', '/api/staffer-route-result?id=' + started['search_id'])
    assert status == 200 and polled['status'] == 'searching' and len(polled['partial_routes']) == 1
    assert polled['partial_routes'][0]['nodes'] and polled['partial_routes'][0]['hops']

    status, answer = http_server.request('POST', '/api/staffer-route-cancel', {'id': started['search_id']})
    assert status == 200 and answer['status'] == 'cancelled'
    status, polled = http_server.request('GET', '/api/staffer-route-result?id=' + started['search_id'])
    assert polled['status'] == 'cancelled' and polled['cancel_reason'] == 'requested'
    assert 'partial_routes' not in polled

    router.block = set()
    status, other = http_server.request('GET', query.replace('target=d', 'target=c') + '&supersedes=' + started['search_id'])
    assert status == 200 and other['search_id'] != started['search_id']
    assert job_of(service, other)['future'].result(timeout=10)['status'] == 'found'
    status, finished = http_server.request('GET', '/api/staffer-route-result?id=' + other['search_id'])
    assert finished['status'] == 'found' and finished['optimal'] and 'partial_routes' not in finished
