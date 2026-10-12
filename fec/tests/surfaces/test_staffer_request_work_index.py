"""One search request, one work index; a released router stores nothing (the v4.1 fix round, finding A1 of the Opus re-check).

Distinct and independent alternatives call ClosenessRoutes.route once per pass, and each call asked the router's table for its work
index. The service releases a router it replaces (AdvocacyRouter.release_search_state empties the table), so a publish that landed
between two passes of a request still running on the replaced router made the next pass build the whole index again: tens of seconds on
the live records, for one request, on a router nothing was going to use again.

Now route_many opens a request scope (staffer_closeness_routes.request_work) around the searches that run more than one pass, and
ClosenessRoutes.index gives a request the index it took first for every later pass. A released ClosenessRoutes stores no index and no
hop-row cache it builds (an in-flight build that finishes after the release is not put back either), and still answers every request
that reaches it. Every check below compares the answer with the same request on an untouched twin router.

    python -m pytest fec/tests/surfaces/test_staffer_request_work_index.py -q
"""
from collections import Counter
import contextlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
import random
import threading
import time
import traceback

import pytest

from fec.surfaces import staffer_closeness_routes as closeness
from fec.surfaces import staffer_distinct_routes
from fec.surfaces import staffer_work_sources
from fec.surfaces.staffer_closeness_routes import ClosenessRoutes
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.test_staffer_closeness_bidirectional import roster, synthetic_router

SEED, PEOPLE, OFFICES = 23, 300, 14
TIMING = {'elapsed_ms', 'search_elapsed_ms', 'expanded', 'neighbor_checks', 'pairs_scored', 'cache_hit', 'response_elapsed_ms'}
STRATEGIES = ('distinct', 'independent')
MINIMUM = {'distinct': 3, 'independent': 2}   # routes a request needs in these worlds to run more than two passes (independent excludes more)


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in TIMING}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def canon(answer):
    return json.dumps(strip(answer), sort_keys=True, default=str)


_OFFSETS = iter(range(1, 10 ** 9))
_OFFSET_LOCK = threading.Lock()


def ask(router, request):
    """The request as the page makes it. `offset` is no part of any cost: it keeps the finished-result cache from answering a repeat."""
    origin, targets, options = request
    with _OFFSET_LOCK:
        offset = next(_OFFSETS)
    return router.route_many(origin, list(targets), {**options, 'offset': offset})


def worlds(people=PEOPLE):
    """Two identical worlds: one is asked untouched (the expected answers), the other is released while it answers."""
    twin, ids, offices = synthetic_router(SEED, people=people, offices=OFFICES)
    live, _, _ = synthetic_router(SEED, people=people, offices=OFFICES)
    return twin, live, ids, offices


def warm(router):
    router.closeness().index(router._options({'objective': 'work_time'}))


def a_request_with_passes(twin, ids, offices, strategy):
    """A seeded request that the twin answers with at least MINIMUM[strategy] routes: so one that runs at least that many passes."""
    minimum = MINIMUM[strategy]
    rng = random.Random(7)
    for _ in range(400):
        origin = rng.choice(ids)
        if rng.random() < .5:
            office = rng.choice(offices)
            staff = [p for p in roster(twin, office) if p != origin]
            if len(staff) < minimum:
                continue
            request = (origin, tuple(staff), {'objective': 'work_time', '_target_office': office, 'route_count': 3, 'alternative_strategy': strategy})
        else:
            request = (origin, (rng.choice([p for p in ids if p != origin]),), {'objective': 'work_time', 'route_count': 3, 'alternative_strategy': strategy})
        answer = ask(twin, request)
        if answer['status'] == 'found' and answer['routes_returned'] >= minimum:
            return request
    raise AssertionError('no request with %d routes found in this world' % minimum)


class Probe:
    """Counts what one request costs the router it runs on: work index builds, the index object each index() call returned, and hop-row
    caches created."""

    def __init__(self, monkeypatch):
        self.builds, self.fetched, self.row_caches = 0, [], 0
        self.lock = threading.Lock()
        real_build, real_index, real_rows = staffer_work_sources.build_index, ClosenessRoutes.index, closeness._RowCache
        probe = self

        def build(router, options):
            with probe.lock:
                probe.builds += 1
            return real_build(router, options)

        def index(routes, options):
            found = real_index(routes, options)
            probe.fetched.append(id(found))
            return found

        class CountedRows(real_rows):
            def __init__(self, *args, **kwargs):
                with probe.lock:
                    probe.row_caches += 1
                super().__init__(*args, **kwargs)
        monkeypatch.setattr(staffer_work_sources, 'build_index', build)
        monkeypatch.setattr(ClosenessRoutes, 'index', index)
        monkeypatch.setattr(closeness, '_RowCache', CountedRows)

    def reset(self):
        self.builds, self.fetched, self.row_caches = 0, [], 0


def release_after_the_first_pass(monkeypatch, router):
    """A publish lands between the first and the second pass of the request: the router the service replaced is released."""
    routes = router.closeness()
    real_route, passes = routes.route, []

    def route(*args, **kwargs):
        result = real_route(*args, **kwargs)
        passes.append(1)
        if len(passes) == 1:
            router.release_search_state()
        return result
    monkeypatch.setattr(routes, 'route', route)
    return passes


# --------------------------------------------------------------------------- a release between the passes
@pytest.mark.parametrize('strategy', STRATEGIES)
def test_a_release_between_the_passes_costs_no_rebuild_and_changes_no_answer(strategy, monkeypatch):
    twin, live, ids, offices = worlds()
    request = a_request_with_passes(twin, ids, offices, strategy)
    expected = canon(ask(twin, request))
    warm(live)
    probe = Probe(monkeypatch)
    passes = release_after_the_first_pass(monkeypatch, live)
    answer = ask(live, request)
    assert live.closeness_routes.released and len(passes) >= MINIMUM[strategy], 'the release landed after the first of several passes'
    assert probe.builds == 0, 'no pass built the index again'
    assert len(probe.fetched) == len(passes) and len(set(probe.fetched)) == 1, 'every pass used the one index the request took first'
    assert canon(answer) == expected
    assert live.closeness_routes.indexes == {} and live.closeness_routes.row_caches == {}, 'and the released router stored nothing'


@pytest.mark.parametrize('strategy', STRATEGIES)
def test_without_the_request_scope_the_same_release_makes_the_next_pass_build_again(strategy, monkeypatch):
    """Negative control: route_many as it was in v4 (no scope). The answer is right, and a pass pays for a whole index."""
    twin, live, ids, offices = worlds()
    request = a_request_with_passes(twin, ids, offices, strategy)
    expected = canon(ask(twin, request))
    warm(live)
    probe = Probe(monkeypatch)
    monkeypatch.setattr(AdvocacyRouter, '_one_work_index', staticmethod(contextlib.nullcontext))
    passes = release_after_the_first_pass(monkeypatch, live)
    answer = ask(live, request)
    assert canon(answer) == expected
    assert len(passes) >= MINIMUM[strategy] and probe.builds >= 1 and len(set(probe.fetched)) >= 2


def test_a_release_inside_a_ranked_request_costs_no_rebuild(monkeypatch):
    twin, live, ids, offices = worlds()
    rng = random.Random(3)
    origin, target = rng.sample(ids, 2)
    request = (origin, (target,), {'objective': 'work_time', 'route_count': 3, 'alternative_strategy': 'ranked'})
    expected = canon(ask(twin, request))
    warm(live)
    probe = Probe(monkeypatch)
    real_evidence, fired = closeness._full_evidence, []

    def releasing(*args, **kwargs):
        if not fired:
            fired.append(1)
            live.release_search_state()
        return real_evidence(*args, **kwargs)
    monkeypatch.setattr(closeness, '_full_evidence', releasing)
    answer = ask(live, request)
    assert fired and probe.builds == 0 and len(set(probe.fetched)) == 1
    assert canon(answer) == expected


# --------------------------------------------------------------------------- a request that starts on a released router
@pytest.mark.parametrize('strategy', STRATEGIES)
def test_a_request_on_a_released_router_builds_one_index_for_all_its_passes_and_the_router_keeps_nothing(strategy, monkeypatch):
    twin, live, ids, offices = worlds()
    request = a_request_with_passes(twin, ids, offices, strategy)
    expected = canon(ask(twin, request))
    warm(live)
    live.release_search_state()
    probe = Probe(monkeypatch)
    answer = ask(live, request)
    assert canon(answer) == expected
    assert probe.builds == 1, 'one index for the request, not one per pass'
    assert len(probe.fetched) >= MINIMUM[strategy] and len(set(probe.fetched)) == 1
    assert probe.row_caches == 1, 'and one hop-row cache, shared by its passes'
    assert live.closeness_routes.indexes == {} and live.closeness_routes.row_caches == {}
    again = ask(live, request)                       # the next request pays for its own: nothing was kept
    assert canon(again) == expected and probe.builds == 2 and live.closeness_routes.indexes == {}


@pytest.mark.parametrize('strategy', STRATEGIES)
def test_without_the_request_scope_every_pass_on_a_released_router_builds_its_own_index_and_rows(strategy, monkeypatch):
    twin, live, ids, offices = worlds()
    request = a_request_with_passes(twin, ids, offices, strategy)
    expected = canon(ask(twin, request))
    live.release_search_state()
    probe = Probe(monkeypatch)
    monkeypatch.setattr(AdvocacyRouter, '_one_work_index', staticmethod(contextlib.nullcontext))
    answer = ask(live, request)
    assert canon(answer) == expected
    assert probe.builds >= MINIMUM[strategy] and probe.row_caches == probe.builds


@pytest.mark.parametrize('request_kind', ['single', 'office', 'ranked'])
def test_a_released_router_answers_every_kind_of_request_as_an_untouched_one_does(request_kind):
    twin, live, ids, offices = worlds()
    rng = random.Random(11)
    origin = rng.choice(ids)
    if request_kind == 'office':
        office = offices[0]
        request = (origin, tuple(p for p in roster(twin, office) if p != origin), {'objective': 'work_time', '_target_office': office, 'route_count': 1})
    else:
        request = (origin, (rng.choice([p for p in ids if p != origin]),),
                   {'objective': 'work_time', 'route_count': 3 if request_kind == 'ranked' else 1, 'alternative_strategy': 'ranked'})
    expected = canon(ask(twin, request))
    live.release_search_state()
    assert canon(ask(live, request)) == expected
    assert live.closeness_routes.indexes == {} and live.closeness_routes.row_caches == {}


# --------------------------------------------------------------------------- a build that is still running when the release lands
class Gate:
    """Stands in for staffer_work_sources.build_index: holds every build until the test lets it go."""

    def __init__(self, monkeypatch):
        self.real, self.calls = staffer_work_sources.build_index, 0
        self.started, self.release = threading.Event(), threading.Event()
        monkeypatch.setattr(staffer_work_sources, 'build_index', self)

    def __call__(self, router, options):
        self.calls += 1
        self.started.set()
        assert self.release.wait(20), 'the test never released the build'
        return self.real(router, options)


def test_an_index_whose_build_finishes_after_the_release_goes_to_the_callers_waiting_on_it_and_is_not_put_back(monkeypatch):
    router, _, _ = synthetic_router(SEED, people=60, offices=6)
    routes = ClosenessRoutes(router)
    options = router._options({'objective': 'work_time'})
    gate = Gate(monkeypatch)
    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(routes.index, options) for _ in range(3)]
        assert gate.started.wait(10)
        time.sleep(.2)
        routes.release()                     # the router is replaced while its index is being built
        gate.release.set()
        built = [future.result(20) for future in futures]
    assert gate.calls == 1 and len({id(index) for index in built}) == 1, 'one build, and every waiting caller got its index'
    assert routes.indexes == {} and routes.index_flights == {}, 'the released router did not keep it'
    again = routes.index(options)
    assert gate.calls == 2 and again is not built[0] and routes.indexes == {}, 'the next caller builds its own'


def test_a_router_that_is_not_released_still_keeps_its_index_and_hop_rows(monkeypatch):
    twin, _, ids, offices = worlds()
    probe = Probe(monkeypatch)
    request = (ids[0], (ids[7],), {'objective': 'work_time', 'route_count': 1})
    ask(twin, request)
    ask(twin, request)
    assert probe.builds == 1 and probe.row_caches == 1, 'the second request used what the first left'
    assert len(twin.closeness_routes.indexes) == 1 and len(twin.closeness_routes.row_caches) == 1 and not twin.closeness_routes.released


# --------------------------------------------------------------------------- the scope itself
def test_the_scope_is_per_request_nests_and_closes_however_the_request_ends():
    assert closeness._REQUEST_WORK.get() is None
    with closeness.request_work():
        outer = closeness._REQUEST_WORK.get()
        assert outer is not None
        with closeness.request_work():
            assert closeness._REQUEST_WORK.get() is outer, 'a scope inside the request\'s own is that scope'
        assert closeness._REQUEST_WORK.get() is outer
        seen = []
        thread = threading.Thread(target=lambda: seen.append(closeness._REQUEST_WORK.get()))
        thread.start()
        thread.join()
        assert seen == [None], 'a request on another thread has its own scope, not this one'
    assert closeness._REQUEST_WORK.get() is None
    with pytest.raises(ZeroDivisionError):
        with closeness.request_work():
            assert closeness._REQUEST_WORK.get() is not None
            1 / 0
    assert closeness._REQUEST_WORK.get() is None, 'an error ends the scope too'


def test_route_many_opens_the_scope_around_the_searches_that_run_more_than_one_pass_and_around_nothing_else(monkeypatch):
    router, ids, offices = synthetic_router(SEED, people=80, offices=6)
    open_in = {}
    real_distinct, real_alternatives, real_route = staffer_distinct_routes.distinct_routes, AdvocacyRouter._route_alternatives, ClosenessRoutes.route

    def distinct(*args, **kwargs):
        open_in['distinct'] = closeness._REQUEST_WORK.get() is not None
        return real_distinct(*args, **kwargs)

    def alternatives(self, *args, **kwargs):
        open_in['yen'] = closeness._REQUEST_WORK.get() is not None
        return real_alternatives(self, *args, **kwargs)

    def route(self, *args, **kwargs):
        open_in.setdefault('search', []).append(closeness._REQUEST_WORK.get() is not None)
        return real_route(self, *args, **kwargs)
    monkeypatch.setattr(staffer_distinct_routes, 'distinct_routes', distinct)
    monkeypatch.setattr(AdvocacyRouter, '_route_alternatives', alternatives)
    monkeypatch.setattr(ClosenessRoutes, 'route', route)
    router.route_many(ids[0], [ids[9]], {'objective': 'work_time', 'route_count': 1})
    router.route_many(ids[0], [ids[9]], {'objective': 'work_time', 'route_count': 3, 'alternative_strategy': 'ranked'})
    assert open_in == {'search': [False, False]}, 'one search call per request: it takes its index once, so it needs no scope'
    open_in.clear()
    router.route_many(ids[0], [ids[9]], {'objective': 'work_time', 'route_count': 3, 'alternative_strategy': 'distinct'})
    assert open_in['distinct'] is True and open_in['search'] and all(open_in['search']), 'every pass of a distinct request runs inside the scope'
    open_in.clear()
    router.route_many(ids[0], [ids[9]], {'objective': 'evidence_cost', 'route_count': 2, 'alternative_strategy': 'ranked'})
    assert open_in == {'yen': True}
    assert closeness._REQUEST_WORK.get() is None, 'and every scope was closed'


# --------------------------------------------------------------------------- concurrent requests and releases
def test_requests_and_releases_in_other_threads_never_change_an_answer_or_rebuild_in_the_middle_of_a_request(monkeypatch):
    """A short run of the stress test the re-check made (6 workers, a releaser every 0-15 ms, a name-index forgetter) at a smaller size:
    every answer equals the untouched twin's, no thread errs, and every request uses one index object from its first pass to its last."""
    twin, live, ids, offices = worlds(people=250)
    rng = random.Random(5)
    requests = []
    for k in range(18):
        origin = rng.choice(ids)
        if k % 2:
            office = rng.choice(offices)
            staff = [p for p in roster(twin, office) if p != origin]
            if not staff:
                continue
            base = (origin, tuple(staff), {'objective': 'work_time', '_target_office': office})
        else:
            base = (origin, (rng.choice([p for p in ids if p != origin]),), {'objective': 'work_time'})
        kind = ('single', 'distinct', 'independent', 'ranked')[k % 4]
        options = dict(base[2])
        if kind != 'single':
            options.update(route_count=3, alternative_strategy=kind)
        requests.append((kind, (base[0], base[1], options)))
    expected = [canon(ask(twin, request)) for _, request in requests]
    seen = threading.local()
    real_index = ClosenessRoutes.index

    def tracking(routes, options):
        found = real_index(routes, options)
        used = getattr(seen, 'ids', None)
        if used is not None:
            used.append(id(found))
        return found
    monkeypatch.setattr(ClosenessRoutes, 'index', tracking)
    stop = threading.Event()
    errors, mismatches, rebuilt, done = [], [], Counter(), Counter()

    def worker(number):
        local = random.Random(number)
        while not stop.is_set():
            i = local.randrange(len(requests))
            seen.ids = []
            try:
                answer = ask(live, requests[i][1])
            except Exception:
                errors.append(traceback.format_exc())
                continue
            finally:
                used, seen.ids = seen.ids, None
            if len(set(used)) > 1:
                rebuilt[requests[i][0]] += 1
            if canon(answer) != expected[i]:
                mismatches.append(i)
            done[requests[i][0]] += 1

    def releaser():
        local = random.Random(99)
        while not stop.is_set():
            time.sleep(local.uniform(0, .015))
            try:
                live.release_search_state()
                if local.random() < .3:
                    live.forget_contact_groups()
            except Exception:
                errors.append(traceback.format_exc())
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)] + [threading.Thread(target=releaser)]
    for thread in threads:
        thread.start()
    time.sleep(float(os.environ.get('STAFFER_STRESS_SECONDS', '4')))
    stop.set()
    for thread in threads:
        thread.join(120)
    assert not errors, errors[:1]
    assert not mismatches, mismatches[:5]
    assert not rebuilt, 'requests that used more than one index object: %s' % dict(rebuilt)
    assert sum(done.values()) >= 10 and set(done) >= {'single', 'distinct'}, dict(done)
