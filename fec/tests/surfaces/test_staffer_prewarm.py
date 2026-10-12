"""Prewarm: after a router is published the service builds, off the request path, what the first search would build.

The work index for the page's default options, then the people index, one after the other, in a background thread
that never holds the service lock while it builds. A request that arrives during a build waits for that build (the work
index has a per-key single-flight, the people index its own lock) and is answered exactly as a lazy build answers it.
"""
from concurrent.futures import ThreadPoolExecutor
import gc
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import time
import weakref

import pytest

from fec.surfaces import staffer_identity_resolution as resolution
from fec.surfaces import staffer_live_service as live
from fec.surfaces import staffer_work_sources
from fec.surfaces.staffer_closeness_routes import ClosenessRoutes
from fec.surfaces.staffer_distinct_routes import contact_group
from fec.surfaces.staffer_live_server import Handler
from fec.surfaces.staffer_live_service import LiveService
from fec.surfaces.staffer_route_cancel import RouteCancelled, cancel_scope
from fec.surfaces.test_staffer_live_service import ProfilesFixture, fixture_graph
from fec.tests.surfaces.test_staffer_rebuild_view import References, merged
from fec.tests.surfaces.test_staffer_route_latency import routed_service, work_route

DEFAULT_KEY = (True, False, True, 'payroll_and_directory')   # include_review, include_unresolved, include_historical, work_sources
TIMING = {'elapsed_ms', 'search_elapsed_ms', 'expanded', 'neighbor_checks', 'pairs_scored', 'cache_hit'}


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in TIMING}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def prewarm_threads():
    return [t for t in threading.enumerate() if t.name == 'staffer-prewarm']


def settle(timeout=15):
    """Wait for every prewarm thread to end; a test never leaves one behind."""
    deadline = time.monotonic() + timeout
    for thread in prewarm_threads():
        thread.join(max(0.1, deadline - time.monotonic()))
    assert not prewarm_threads(), 'a prewarm thread is still running'


@pytest.fixture(autouse=True)
def no_leftover_threads():
    yield
    settle()


def prewarmed_service():
    service = routed_service()
    service.prewarm_enabled = True
    return service


class Gate:
    """Stands in for staffer_work_sources.build_index: counts the builds and holds each one until released."""

    def __init__(self, monkeypatch, fail=None):
        self.real, self.fail = staffer_work_sources.build_index, fail
        self.calls, self.lock = 0, threading.Lock()
        self.started, self.release = threading.Event(), threading.Event()
        monkeypatch.setattr(staffer_work_sources, 'build_index', self)

    def __call__(self, router, options):
        with self.lock:
            self.calls += 1
        self.started.set()
        assert self.release.wait(20), 'the test never released the build'
        if self.fail is not None:
            raise self.fail
        return self.real(router, options)


# --------------------------------------------------------------------------- what runs, and in what order
def test_prewarm_builds_the_work_index_then_the_people_index_one_after_the_other(monkeypatch):
    service = prewarmed_service()
    trace, real_build, real_network = [], staffer_work_sources.build_index, LiveService.network_index

    def traced_build(router, options):
        trace.append('work_index start')
        try:
            return real_build(router, options)
        finally:
            trace.append('work_index end')

    def traced_network(self, *args, **kwargs):
        trace.append('network_index start')
        try:
            return real_network(self, *args, **kwargs)
        finally:
            trace.append('network_index end')
    monkeypatch.setattr(staffer_work_sources, 'build_index', traced_build)
    monkeypatch.setattr(LiveService, 'network_index', traced_network)
    router = service.router
    assert DEFAULT_KEY not in router.closeness_routes.indexes and service._explorer_index is None
    service._start_prewarm()
    settle()
    assert trace == ['work_index start', 'work_index end', 'network_index start', 'network_index end']
    assert DEFAULT_KEY in router.closeness_routes.indexes, 'the page defaults are the key the first search asks for'
    assert service._explorer_index['revision'] == service.revision
    state = service.metrics['prewarm']
    assert state['status'] == 'done' and state['enabled'] and state['earlier'] == []
    assert list(state['stages']) == ['work_index', 'network_index']
    assert all(stage['status'] == 'done' and stage['ms'] >= 0 for stage in state['stages'].values())
    assert state['total_ms'] >= max(stage['ms'] for stage in state['stages'].values())
    assert (state['router_revision'], state['as_of']) == (router.revision, router.as_of)
    json.dumps(service.metrics)


def test_the_work_index_prewarm_uses_the_key_a_default_search_asks_for():
    service = prewarmed_service()
    service._start_prewarm()
    settle()
    index = service.router.closeness_routes.indexes[DEFAULT_KEY]
    answered = work_route(service, 1)
    assert answered['status'] == 'found' and service.router.closeness_routes.indexes[DEFAULT_KEY] is index, 'the search used the prewarmed index'
    assert list(service.router.closeness_routes.indexes) == [DEFAULT_KEY]


def test_a_service_that_was_not_started_never_prewarms():
    service = routed_service()
    assert LiveService.prewarm_enabled is False and service.prewarm_enabled is False
    service._refresh_profiles(force=True)
    service._start_prewarm()
    assert not prewarm_threads() and 'prewarm' not in service.metrics
    assert DEFAULT_KEY not in service.router.closeness_routes.indexes


def test_start_turns_prewarm_on_and_the_environment_can_turn_it_off(monkeypatch):
    monkeypatch.setattr(LiveService, '_run', lambda self: None)
    monkeypatch.delenv('MELANGE_STAFFER_PREWARM', raising=False)
    on = LiveService('fixture', fixture_graph())
    on.start()
    assert on.prewarm_enabled and on.metrics['prewarm'] == {'enabled': True, 'status': 'waiting', 'stages': {}, 'earlier': []}
    for value in ('0', 'off', 'False', ' no '):
        monkeypatch.setenv('MELANGE_STAFFER_PREWARM', value)
        off = LiveService('fixture', fixture_graph())
        off.start()
        assert not off.prewarm_enabled and off.metrics['prewarm']['status'] == 'disabled', value
    monkeypatch.setenv('MELANGE_STAFFER_PREWARM', '1')
    other = LiveService('fixture', fixture_graph())
    other.start()
    assert other.prewarm_enabled


def test_prewarm_waits_for_a_service_that_is_not_ready():
    service = prewarmed_service()
    service.ready = False
    service._start_prewarm()
    assert not prewarm_threads() and 'prewarm' not in service.metrics
    service.ready, service._assertion_update_pending = True, True
    service._start_prewarm()
    assert not prewarm_threads()


# --------------------------------------------------------------------------- a request meets a build in progress
def test_a_request_during_the_prewarm_waits_for_that_build_and_gets_the_same_answer(monkeypatch):
    expected = work_route(routed_service(), 1)                      # a lazy build, no prewarm
    service = prewarmed_service()
    gate = Gate(monkeypatch)
    service._start_prewarm()
    assert gate.started.wait(10), 'the prewarm is inside the work index build'
    answers = []
    request = threading.Thread(target=lambda: answers.append(work_route(service, 1)), daemon=True)
    request.start()
    request.join(.6)
    assert request.is_alive() and not answers, 'the request waits for the build; it does not answer, or start another'
    assert gate.calls == 1
    gate.release.set()
    request.join(15)
    settle()
    assert not request.is_alive() and len(answers) == 1
    assert gate.calls == 1, 'one build served the prewarm and the request'
    assert strip(answers[0]) == strip(expected)
    assert json.dumps(strip(answers[0]), sort_keys=True) == json.dumps(strip(expected), sort_keys=True)


def test_a_request_that_starts_the_build_is_waited_for_by_the_prewarm(monkeypatch):
    service = prewarmed_service()
    gate = Gate(monkeypatch)
    answers = []
    request = threading.Thread(target=lambda: answers.append(work_route(service, 1)), daemon=True)
    request.start()
    assert gate.started.wait(10)
    service._start_prewarm()
    time.sleep(.3)
    assert gate.calls == 1, 'the prewarm waits for the request\'s build'
    gate.release.set()
    request.join(15)
    settle()
    assert gate.calls == 1 and len(answers) == 1 and service.metrics['prewarm']['status'] == 'done'


def test_concurrent_callers_of_the_work_index_share_one_build(monkeypatch):
    router = routed_service().router
    routes = ClosenessRoutes(router)
    options = router._options({'objective': 'work_time'})
    gate = Gate(monkeypatch)
    with ThreadPoolExecutor(8) as pool:
        futures = [pool.submit(routes.index, options) for _ in range(8)]
        assert gate.started.wait(10)
        time.sleep(.3)
        assert gate.calls == 1 and not any(f.done() for f in futures)
        gate.release.set()
        built = [f.result(15) for f in futures]
    assert gate.calls == 1 and all(index is built[0] for index in built)
    assert routes.index_flights == {} and list(routes.indexes) == [DEFAULT_KEY]
    assert routes.index(options) is built[0] and gate.calls == 1


def test_a_failed_build_reaches_every_waiter_and_is_retried_by_the_next_caller(monkeypatch):
    router = routed_service().router
    routes = ClosenessRoutes(router)
    options = router._options({'objective': 'work_time'})
    gate = Gate(monkeypatch, fail=RuntimeError('the build failed'))
    with ThreadPoolExecutor(5) as pool:
        futures = [pool.submit(routes.index, options) for _ in range(5)]
        assert gate.started.wait(10)
        time.sleep(.3)
        gate.release.set()
        for future in futures:
            with pytest.raises(RuntimeError, match='the build failed'):
                future.result(15)
    assert gate.calls == 1 and routes.index_flights == {} and routes.indexes == {}
    gate.fail = None
    index = routes.index(options)
    assert gate.calls == 2 and routes.indexes[DEFAULT_KEY] is index


def test_a_waiting_search_can_be_cancelled_while_the_build_goes_on(monkeypatch):
    router = routed_service().router
    routes = ClosenessRoutes(router)
    options = router._options({'objective': 'work_time'})
    gate = Gate(monkeypatch)
    builder = threading.Thread(target=lambda: routes.index(options), daemon=True)
    builder.start()
    assert gate.started.wait(10)
    cancel, outcome = threading.Event(), []

    def wait_for_it():
        with cancel_scope(cancel):
            try:
                routes.index(options)
            except RouteCancelled:
                outcome.append('cancelled')
    waiter = threading.Thread(target=wait_for_it, daemon=True)
    waiter.start()
    time.sleep(.3)
    cancel.set()
    waiter.join(5)
    assert outcome == ['cancelled'] and not waiter.is_alive()
    gate.release.set()
    builder.join(15)
    assert gate.calls == 1 and DEFAULT_KEY in routes.indexes, 'the build finished for everyone else'


# --------------------------------------------------------------------------- a replaced router
def test_a_router_swap_during_the_prewarm_discards_it(monkeypatch):
    service = prewarmed_service()
    gate = Gate(monkeypatch)
    network_builds = []
    real_network = LiveService.network_index
    monkeypatch.setattr(LiveService, 'network_index', lambda self, *a, **k: network_builds.append(self.router) or real_network(self, *a, **k))
    old = service.router
    service._start_prewarm()
    assert gate.started.wait(10)
    service._refresh_profiles(force=True)          # a new router is published: the old router's prewarm is now stale
    new = service.router
    assert new is not old
    gate.release.set()
    settle()
    assert network_builds == [new], 'the stale prewarm never reached the people index; the new router\'s prewarm built it once'
    state = service.metrics['prewarm']
    assert state['status'] == 'done' and state['seq'] == 2
    assert [(e['seq'], e['status'], e.get('abandoned_before')) for e in state['earlier']] == [(1, 'abandoned', 'network_index')]
    assert list(state['earlier'][0]['stages']) == ['work_index'], 'it had finished the stage it was in, and no more'
    assert DEFAULT_KEY in new.closeness_routes.indexes, 'the new router built its own index'
    assert old.closeness_routes.indexes == {}, 'the replaced router was released while its build ran, so the finished build was not put back into it'
    assert service._explorer_index['revision'] == service.revision


def test_several_swaps_build_only_for_the_router_that_is_current_when_each_build_starts(monkeypatch):
    service = prewarmed_service()
    gate = Gate(monkeypatch)
    service._start_prewarm()
    assert gate.started.wait(10)
    for _ in range(3):
        service._refresh_profiles(force=True)
    final = service.router
    gate.release.set()
    settle()
    assert gate.calls == 2, 'the first router (already building) and the last one; the two in between were never built'
    state = service.metrics['prewarm']
    assert state['status'] == 'done' and state['seq'] == 4
    assert sorted(e['status'] for e in state['earlier']) == ['abandoned', 'abandoned', 'abandoned']
    assert DEFAULT_KEY in final.closeness_routes.indexes


def test_the_daily_rebuild_prewarms_the_new_router(monkeypatch):
    service = prewarmed_service()
    service._start_prewarm()
    settle()
    first = service.router
    monkeypatch.setattr(live, 'local_date', lambda: '2031-01-02')          # the day changes, so as_of does and the router is rebuilt
    service._refresh_profiles()
    settle()
    assert service.router is not first and service.router.as_of == '2031-01-02'
    state = service.metrics['prewarm']
    assert state['status'] == 'done' and state['as_of'] == '2031-01-02' and state['seq'] == 2
    assert DEFAULT_KEY in service.router.closeness_routes.indexes
    assert [e['as_of'] for e in state['earlier']] == [first.as_of]


def test_an_equal_poll_does_not_prewarm_again():
    service = prewarmed_service()
    service._start_prewarm()
    settle()
    service._refresh_profiles()
    settle()
    assert service._prewarm_seq == 1


def test_the_identity_rebuild_prewarms_the_router_it_publishes_and_its_working_copy_does_not(tmp_path, monkeypatch):
    assertions = tmp_path / 'assertions.json'
    assertions.write_text(json.dumps({'keep_separate_pairs': [], 'coworker_assertions': []}))
    monkeypatch.setattr(resolution, 'USER_ASSERTIONS', assertions)
    graph = fixture_graph()
    service = LiveService(tmp_path, graph)
    service.profiles = ProfilesFixture()
    service.profiles.path = tmp_path / 'profiles.jsonl'
    service.identity_web = References(tmp_path / 'references.sqlite')
    service.evidence = {'episodes': [], 'user_assertions': []}
    service._source_nodes, service._source_edges, service._source_episodes = dict(graph.nodes), list(graph.edges), []
    service._refresh_profiles(force=True)
    service.ready, service.stage = True, 'Ready'
    monkeypatch.setattr(live, 'load_inventory', lambda *args: {'nodes': {}, 'identity_aliases': {}, 'identity_groups': [], 'sources': []})
    monkeypatch.setattr(live, 'load_identity_resolution', lambda *args: {'records': {}, 'candidate_groups': [], 'sources': [], 'counts': {}})
    monkeypatch.setattr(live, 'resolve_identities', lambda *args: merged())
    service.prewarm_enabled = True
    before = service.router
    service._rebuild_identity()
    settle()
    assert service.router is not before
    assert service._prewarm_seq == 1, 'one prewarm, for the router the rebuild published; the working copy never queues its own'
    state = service.metrics['prewarm']
    assert state['status'] == 'done' and state['router_revision'] == service.router.revision
    assert DEFAULT_KEY in service.router.closeness_routes.indexes
    assert service._explorer_index['revision'] == service.revision


# --------------------------------------------------------------------------- the replaced router's memory
# D-087: memory headroom on this host is the binding constraint. When the service publishes a new router it lets go of what the
# replaced one kept for its searches (the work indexes, the hop-row caches, the name index), and the prewarm that follows a replacement
# runs one collection before it builds (a prewarm at a start follows none, and runs none: nothing is freed and a collection stalls every
# request thread), so the old router's indexes and the new router's are not meant to be resident together. No answer changes.
def weak_refs_to_the_work_state(service):
    """Weak references to the router the service holds, its default work index and one of its hop-row caches.

    A function, so that no local of the calling test keeps any of the three alive."""
    router = service.router
    routes = router.closeness_routes
    return weakref.ref(router), weakref.ref(routes.indexes[DEFAULT_KEY]), weakref.ref(next(iter(routes.row_caches.values())))


def a_prewarmed_service_that_has_served_a_search():
    service = prewarmed_service()
    service._start_prewarm()
    settle()
    work_route(service, 1)           # a search on the page defaults fills the hop-row cache
    return service


def test_publishing_a_new_router_lets_go_of_the_replaced_routers_work_index_row_cache_and_name_index():
    service = a_prewarmed_service_that_has_served_a_search()
    contact_group(service.router, {'person:gordon'})          # the name index (avoid_people, distinct routes) exists on the router
    assert service.router._contact_groups is not None
    router_ref, index_ref, rows_ref = weak_refs_to_the_work_state(service)
    assert index_ref() is not None and rows_ref() is not None
    service.prewarm_enabled = False          # only the publish: no prewarm thread, so no collection either
    gc.disable()                             # and no automatic one, so the replaced router can still be looked at
    try:
        service._refresh_profiles(force=True)
        assert index_ref() is None and rows_ref() is None, 'the publish released them, before anything collects'
        replaced = router_ref()
        assert replaced is not None and replaced is not service.router, 'the router itself waits for a collection'
        assert replaced.closeness_routes.indexes == {} and replaced.closeness_routes.row_caches == {}
        assert replaced._contact_groups is None, 'and its name index is gone'
        replaced = None
    finally:
        gc.enable()
    assert service.router.closeness_routes.indexes == {} and service.router.closeness_routes.row_caches == {}, 'the new router has built nothing yet'


def test_the_replaced_router_is_gone_before_the_next_prewarm_builds_anything(monkeypatch):
    service = a_prewarmed_service_that_has_served_a_search()
    router_ref, index_ref, rows_ref = weak_refs_to_the_work_state(service)
    gate = Gate(monkeypatch)                  # holds the new router's work index build
    service._refresh_profiles(force=True)     # publishes the new router and queues its prewarm
    assert gate.started.wait(10), 'the new router\'s prewarm is inside its work index build'
    try:
        assert (router_ref(), index_ref(), rows_ref()) == (None, None, None), 'the old router is not resident while the new index is built'
    finally:
        gate.release.set()
        settle()
    assert service.metrics['prewarm']['status'] == 'done' and service.metrics['prewarm']['gc_ms'] >= 0


def test_without_the_release_and_the_collection_the_old_router_stays_resident_while_the_new_index_builds(monkeypatch):
    """Negative control: the service as it was before the change. Automatic collection is off for the test, so what stays is what
    only a collection (or the release) could free."""
    service = a_prewarmed_service_that_has_served_a_search()
    router_ref, index_ref, rows_ref = weak_refs_to_the_work_state(service)
    monkeypatch.setattr(LiveService, '_release_replaced_router', lambda self, replaced, current: None)
    monkeypatch.setattr(live.gc, 'collect', lambda *args, **kwargs: 0)
    gate = Gate(monkeypatch)
    gc.disable()
    try:
        service._refresh_profiles(force=True)
        assert gate.started.wait(10)
        assert router_ref() is not None and index_ref() is not None and rows_ref() is not None
    finally:
        gc.enable()
        gate.release.set()
        settle()


def test_the_release_frees_the_indexes_and_the_collection_frees_the_router(monkeypatch):
    """Each half alone: the release leaves only the router, which refers to its own search object and so waits for a collection."""
    service = a_prewarmed_service_that_has_served_a_search()
    router_ref, index_ref, rows_ref = weak_refs_to_the_work_state(service)
    monkeypatch.setattr(live.gc, 'collect', lambda *args, **kwargs: 0)
    service.prewarm_enabled = False
    gc.disable()
    try:
        service._refresh_profiles(force=True)
        assert index_ref() is None and rows_ref() is None and router_ref() is not None
    finally:
        gc.enable()
    monkeypatch.undo()
    gc.collect()
    assert router_ref() is None


def traced_prewarm(monkeypatch):
    """What a prewarm does, in order: each collection (the prewarm thread's own), each work index build and each people index request."""
    trace, real_collect = [], gc.collect
    real_build, real_network = staffer_work_sources.build_index, LiveService.network_index

    def collect(*args, **kwargs):
        if threading.current_thread().name == 'staffer-prewarm':
            trace.append('gc')
        return real_collect(*args, **kwargs)
    monkeypatch.setattr(live.gc, 'collect', collect)
    monkeypatch.setattr(staffer_work_sources, 'build_index', lambda router, options: trace.append('work_index') or real_build(router, options))
    monkeypatch.setattr(LiveService, 'network_index', lambda self, *a, **k: trace.append('network_index') or real_network(self, *a, **k))
    return trace


def test_a_prewarm_at_a_start_runs_no_collection_because_no_router_was_replaced(monkeypatch):
    """At a start nothing is freed, and a full collection holds every request thread while it runs: on the live heap it held them for
    10.1 s (gc_ms on /api/staffer-performance), before the 40 s build, for nothing."""
    service = prewarmed_service()
    trace = traced_prewarm(monkeypatch)
    assert service._router_replaced is False, 'the first router the service published replaced nothing'
    service._start_prewarm()
    settle()
    assert trace == ['work_index', 'network_index'], 'the two builds, and no collection'
    state = service.metrics['prewarm']
    assert state['status'] == 'done' and 'gc_ms' not in state
    assert service._router_replaced is False


def test_the_prewarm_after_a_replacement_runs_one_collection_before_its_first_build_and_a_later_one_runs_none(monkeypatch):
    service = prewarmed_service()
    service._start_prewarm()
    settle()                                  # a start: nothing replaced, nothing collected
    trace = traced_prewarm(monkeypatch)
    service._refresh_profiles(force=True)     # publishes a new router: the old one is replaced and released, and the new one's prewarm queued
    settle()
    assert trace == ['gc', 'work_index', 'network_index'], 'one collection, before the first build and not between the stages'
    state = service.metrics['prewarm']
    assert state['seq'] == 2 and state['status'] == 'done' and state['gc_ms'] >= 0
    assert service._router_replaced is False, 'the prewarm that collected has used up the request for one'
    trace.clear()
    service._start_prewarm()                  # the same router again: nothing was replaced since, so nothing to collect
    settle()
    assert trace == ['network_index'] and 'gc_ms' not in service.metrics['prewarm'], trace


def test_prewarms_abandoned_before_they_build_collect_nothing_and_the_one_that_builds_collects_for_every_swap(monkeypatch):
    service = prewarmed_service()
    service._start_prewarm()
    settle()
    trace, real_collect = [], gc.collect

    def collect(*args, **kwargs):
        if threading.current_thread().name == 'staffer-prewarm':
            trace.append('gc')
        return real_collect(*args, **kwargs)
    gate = Gate(monkeypatch)
    monkeypatch.setattr(live.gc, 'collect', collect)
    service._refresh_profiles(force=True)     # the first replacement: its prewarm collects, then builds (held at the gate)
    assert gate.started.wait(10)
    for _ in range(3):                        # three more swaps while it builds: two prewarms are abandoned before any build
        service._refresh_profiles(force=True)
    assert service._router_replaced is True, 'the swaps since the collection are waiting for one'
    gate.release.set()
    settle()
    assert gate.calls == 2 and trace == ['gc', 'gc'], trace   # the first prewarm's, and the last router's
    assert service._router_replaced is False


def test_a_publish_between_the_router_check_and_the_flag_leaves_the_collection_to_the_next_prewarm(monkeypatch):
    """v4.1 LOW-1: a publish landing after the prewarm's router check but before it takes the flag used to let the stale
    prewarm consume the flag (collecting while it still held its router) and the new router's prewarm collect nothing."""
    service = prewarmed_service()
    service._start_prewarm()
    settle()
    trace = traced_prewarm(monkeypatch)
    real_lock, fired = service._prewarm_lock, []

    class Window:
        def __enter__(self):
            import sys
            if sys._getframe(1).f_code.co_name == '_prewarm' and not fired:
                fired.append(1)
                service._refresh_profiles(force=True)   # the publish lands inside the window
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)
    service._prewarm_lock = Window()
    service._refresh_profiles(force=True)              # replaces a router: its prewarm meets the window
    deadline = time.monotonic() + 30                    # the prewarm queued inside the window starts after a first join
    while prewarm_threads() and time.monotonic() < deadline:
        for thread in prewarm_threads():
            thread.join(1)
    settle()
    assert fired == [1]
    assert trace == ['gc', 'work_index', 'network_index'], trace   # the stale prewarm builds nothing; the current one collects
    assert service.metrics['prewarm']['status'] == 'done' and service.metrics['prewarm']['gc_ms'] >= 0
    assert service._router_replaced is False


def test_only_a_router_that_was_actually_replaced_asks_for_a_collection():
    service = routed_service()
    router = service.router
    service._release_replaced_router(None, router)         # the first router of a start
    service._release_replaced_router(router, router)       # the same router published again
    assert service._router_replaced is False
    other = routed_service().router
    service._release_replaced_router(other, router)
    assert service._router_replaced is True and other.closeness_routes.released
    service._router_replaced = False

    class Broken:
        def release_search_state(self):
            raise RuntimeError('release failed')
    service._release_replaced_router(Broken(), router)     # the router was replaced even if letting go of its state failed
    assert service._router_replaced is True and service.metrics['router_release_error'] == 'RuntimeError: release failed'


def test_a_request_that_reaches_the_released_router_builds_its_own_index_and_answers_exactly_as_before(monkeypatch):
    service = a_prewarmed_service_that_has_served_a_search()
    router = service.router
    before = work_route(service, 2)
    held = router.closeness_routes.indexes[DEFAULT_KEY]       # what a search that is running holds
    router.release_search_state()
    assert router.closeness_routes.indexes == {} and router.closeness_routes.row_caches == {}
    builds, real_build = [], staffer_work_sources.build_index
    monkeypatch.setattr(staffer_work_sources, 'build_index', lambda r, o: builds.append(1) or real_build(r, o))
    after = work_route(service, 3)                             # another request: not the finished result, so a search
    assert builds == [1], 'it built an index of its own'
    assert router.closeness_routes.indexes == {} and router.closeness_routes.row_caches == {}, 'and the released router kept neither it nor its rows'
    assert after['neighbor_checks'] > 0 and strip(after) == strip(before)
    assert json.dumps(strip(after), sort_keys=True) == json.dumps(strip(before), sort_keys=True)
    assert held is not None


def test_a_request_waiting_on_the_replaced_routers_build_still_gets_its_answer_and_the_finished_build_is_not_put_back(monkeypatch):
    """The re-check's part 3: the old router's prewarm build is in flight, a request on that router waits on it, a new router is
    published (the old one is released), and then the build finishes. The request is answered as an unreleased router would answer it,
    and the released router does not take the finished index back (it would have held it for as long as anything referred to the router)."""
    service = prewarmed_service()
    gate = Gate(monkeypatch)
    old = service.router
    service._start_prewarm()
    assert gate.started.wait(10)
    answers = []
    request = threading.Thread(target=lambda: answers.append(service.route('person:gordon', 'person:other-gordon', {'objective': 'work_time', 'limit': 501})),
                               daemon=True)
    request.start()                           # it took the old router, and waits on the build the prewarm is running
    time.sleep(.3)
    assert request.is_alive() and not answers
    service._refresh_profiles(force=True)     # publishes a new router: the old one is released while its build is in flight
    assert service.router is not old and old.closeness_routes.released
    gate.release.set()
    request.join(30)
    settle()
    assert answers and answers[0]['status'] == 'found'
    assert gate.calls == 2, 'the old router\'s one build (shared by the prewarm and the request), and the new router\'s'
    assert old.closeness_routes.indexes == {} and old.closeness_routes.row_caches == {}, 'the released router kept nothing'
    assert DEFAULT_KEY in service.router.closeness_routes.indexes
    expected = routed_service().route('person:gordon', 'person:other-gordon', {'objective': 'work_time', 'limit': 501})
    assert strip(answers[0]) == strip(expected)


def test_a_search_takes_its_index_once_so_a_release_in_the_middle_of_it_costs_no_second_build(monkeypatch):
    service = a_prewarmed_service_that_has_served_a_search()
    router = service.router
    routes, builds, calls = router.closeness_routes, [], []
    real_index, real_build = routes.index, staffer_work_sources.build_index

    def releasing(options):                  # a release lands right after the search has its index
        index = real_index(options)
        calls.append(1)
        routes.release()
        return index
    monkeypatch.setattr(routes, 'index', releasing)
    monkeypatch.setattr(staffer_work_sources, 'build_index', lambda r, o: builds.append(1) or real_build(r, o))
    answer = router.route('person:gordon', 'person:other-gordon', {'objective': 'work_time', 'limit': 140})
    assert answer['status'] == 'found' and not answer.get('cache_hit')
    assert calls == [1] and builds == [], 'the search asked once, and so built nothing'


def test_a_publish_that_releases_the_replaced_router_answers_exactly_as_one_that_does_not(monkeypatch):
    def answers(release):
        service = a_prewarmed_service_that_has_served_a_search()
        with monkeypatch.context() as patched:
            if not release:
                patched.setattr(LiveService, '_release_replaced_router', lambda self, replaced, current: None)
            service._refresh_profiles(force=True)
        out = [work_route(service, probe, origin, target) for probe, (origin, target) in
               enumerate([('person:gordon', 'person:other-gordon'), ('person:other-gordon', 'person:gordon'),
                          ('person:gordon', 'person:stephanie')], 10)]
        out.append(service.route('person:gordon', 'person:other-gordon', {'objective': 'work_time', 'route_count': 3, 'avoid_people': ('person:stephanie',)}))
        out.append(service.route('person:gordon', 'person:other-gordon', {'objective': 'evidence_cost', 'avoid_people': ('person:stephanie',)}))
        return [json.dumps(strip(answer), sort_keys=True) for answer in out]
    released, kept = answers(True), answers(False)
    assert released == kept and len(released) == 5


def test_the_identity_rebuild_releases_the_router_it_replaces(tmp_path, monkeypatch):
    assertions = tmp_path / 'assertions.json'
    assertions.write_text(json.dumps({'keep_separate_pairs': [], 'coworker_assertions': []}))
    monkeypatch.setattr(resolution, 'USER_ASSERTIONS', assertions)
    graph = fixture_graph()
    service = LiveService(tmp_path, graph)
    service.profiles = ProfilesFixture()
    service.profiles.path = tmp_path / 'profiles.jsonl'
    service.identity_web = References(tmp_path / 'references.sqlite')
    service.evidence = {'episodes': [], 'user_assertions': []}
    service._source_nodes, service._source_edges, service._source_episodes = dict(graph.nodes), list(graph.edges), []
    service._refresh_profiles(force=True)
    service.ready, service.stage = True, 'Ready'
    monkeypatch.setattr(live, 'load_inventory', lambda *args: {'nodes': {}, 'identity_aliases': {}, 'identity_groups': [], 'sources': []})
    monkeypatch.setattr(live, 'load_identity_resolution', lambda *args: {'records': {}, 'candidate_groups': [], 'sources': [], 'counts': {}})
    monkeypatch.setattr(live, 'resolve_identities', lambda *args: merged())
    old = service.router
    old.closeness().index(old._options({'objective': 'work_time'}))
    held = weakref.ref(old.closeness_routes.indexes[DEFAULT_KEY])
    del old
    service._rebuild_identity()
    assert held() is None, 'the identity rebuild published a new router and released the old one\'s work index'
    assert service.router.closeness_routes.indexes == {}
    assert service._router_replaced is True, 'and asked for the collection that frees the router itself'


# --------------------------------------------------------------------------- the people index is the same one
def test_the_people_index_and_its_etag_are_identical_with_and_without_prewarm(monkeypatch):
    builds = []
    real = live.build_index
    monkeypatch.setattr(live, 'build_index', lambda *args, **kwargs: builds.append(1) or real(*args, **kwargs))
    lazy = routed_service()
    lazy_raw, lazy_etag = lazy.network_index(compressed=False)
    lazy_network, lazy_network_etag = lazy.network_index(compressed=False, scope='network')
    assert len(builds) == 1
    warm = prewarmed_service()
    warm._start_prewarm()
    settle()
    assert len(builds) == 2, 'the prewarm built it'
    warm_raw, warm_etag = warm.network_index(compressed=False)
    warm_network, warm_network_etag = warm.network_index(compressed=False, scope='network')
    assert len(builds) == 2, 'and the first request after it builds nothing'
    assert (warm_etag, warm_raw) == (lazy_etag, lazy_raw)
    assert (warm_network_etag, warm_network) == (lazy_network_etag, lazy_network)
    assert warm_etag.startswith('"index-') and warm_network_etag.startswith('"network-')


def test_a_request_for_the_people_index_during_the_prewarm_waits_for_that_build(monkeypatch):
    service = prewarmed_service()
    started, release, builds = threading.Event(), threading.Event(), []
    real = live.build_index

    def slow(*args, **kwargs):
        builds.append(1)
        started.set()
        assert release.wait(20)
        return real(*args, **kwargs)
    monkeypatch.setattr(live, 'build_index', slow)
    service._start_prewarm()
    assert started.wait(10), 'the prewarm is in the people index build'
    answers = []
    request = threading.Thread(target=lambda: answers.append(service.network_index(compressed=False)), daemon=True)
    request.start()
    request.join(.5)
    assert request.is_alive() and builds == [1], 'the request waits on the one build'
    release.set()
    request.join(15)
    settle()
    assert len(builds) == 1 and answers[0][1].startswith('"index-')


# --------------------------------------------------------------------------- locks and failures
def test_the_service_lock_is_free_while_the_work_index_builds(monkeypatch):
    service = prewarmed_service()
    gate = Gate(monkeypatch)
    service._start_prewarm()
    assert gate.started.wait(10)
    freed = []
    probe = threading.Thread(target=lambda: freed.append(service.lock.acquire(timeout=2)) or service.lock.release(), daemon=True)
    probe.start()
    probe.join(5)
    assert freed == [True], 'a build in progress must not hold the service lock'
    assert service.search('Gordon', 5)['revision'] == service.revision, 'and the service answers while the build runs'
    gate.release.set()
    settle()


def test_the_service_lock_is_free_while_the_people_index_builds(monkeypatch):
    service = prewarmed_service()
    started, release = threading.Event(), threading.Event()
    real = live.build_index

    def slow(*args, **kwargs):
        started.set()
        assert release.wait(20)
        return real(*args, **kwargs)
    monkeypatch.setattr(live, 'build_index', slow)
    service._start_prewarm()
    assert started.wait(10)
    freed = []
    probe = threading.Thread(target=lambda: freed.append(service.lock.acquire(timeout=2)) or service.lock.release(), daemon=True)
    probe.start()
    probe.join(5)
    assert freed == [True]
    release.set()
    settle()


def test_a_failing_stage_is_reported_and_the_next_stage_still_runs(monkeypatch):
    service = prewarmed_service()
    gate = Gate(monkeypatch, fail=RuntimeError('no work index today'))
    gate.release.set()
    service._start_prewarm()
    settle()
    state = service.metrics['prewarm']
    assert state['status'] == 'error'
    assert state['stages']['work_index']['status'] == 'error' and 'no work index today' in state['stages']['work_index']['error']
    assert state['stages']['network_index']['status'] == 'done' and service._explorer_index is not None
    gate.fail = None                       # and a search still builds the index itself
    assert work_route(service, 1)['status'] == 'found' and DEFAULT_KEY in service.router.closeness_routes.indexes


def test_prewarm_never_raises_into_the_service(monkeypatch):
    service = prewarmed_service()
    monkeypatch.setattr(threading.Thread, 'start', lambda self: (_ for _ in ()).throw(RuntimeError("can't start new thread")))
    service._start_prewarm()
    assert service.metrics['prewarm']['status'] == 'error' and "can't start new thread" in service.metrics['prewarm']['error']


# --------------------------------------------------------------------------- /api/staffer-performance
def test_the_performance_endpoint_serves_the_prewarm_state_and_timings():
    service = prewarmed_service()
    service._start_prewarm()
    settle()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.dashboard = service
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
        connection.request('GET', '/api/staffer-performance')
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
    assert response.status == 200
    prewarm = payload['metrics']['prewarm']
    assert prewarm['status'] == 'done' and prewarm['enabled'] is True
    assert set(prewarm['stages']) == {'work_index', 'network_index'}
    assert all(isinstance(stage['ms'], (int, float)) for stage in prewarm['stages'].values())
    assert prewarm['router_revision'] == service.router.revision and prewarm['total_ms'] >= 0
    assert prewarm['queued_utc'] and prewarm['started_utc'] and prewarm['finished_utc']
