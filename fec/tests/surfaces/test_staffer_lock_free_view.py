"""Typeahead and the map read one published view and take no service lock; the reconciliation loop reads its status before it locks.

Every claim has a negative control: the behaviour before the change (copied here as legacy_*) is run through the same harness
and shown to fail it, so a regression to the old behaviour cannot pass unnoticed.
"""
import dataclasses
from copy import deepcopy
import itertools
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from fec.surfaces import staffer_live_service as service_module
from fec.surfaces.staffer_live_service import compact_node, identity_now
from fec.surfaces.staffer_office_routes import search_offices
from fec.tests.surfaces.staffer_latency_fixtures import LockHeld, memo_service, profile_node
from fec.tests.surfaces.test_staffer_search_differential import old_service_search

PROMPT = 0.2  # seconds: a typeahead answer while the lock is held must come at least this fast


# ------------------------------------------- the behaviour before the change, as it was

def legacy_search(service, query, limit=20):
    """LiveService.search before the change: the whole call ran under the service lock."""
    with service.lock:
        if not service.ready or service._assertion_update_pending:
            return {'nodes': [], 'ready': False, 'stage': service.stage}
        query = service._resolve_id(query)
        excluded = getattr(getattr(service.router, 'person_facts', None), 'network_excluded', ())
        nodes = old_service_search(service.static.nodes, service.static.search_ids, service.profile_nodes, service.profile_search,
                                   excluded, query, limit)
        return {'nodes': [compact_node(n) for n in nodes], 'ready': True, 'revision': service.revision}


def legacy_offices(service, query, limit=20):
    with service.lock:
        if not service.ready or service._assertion_update_pending:
            return {'ready': False, 'nodes': [], 'stage': service.stage}
        router = service.router
    return {'ready': True, 'revision': router.revision,
            'nodes': search_offices(router.office_index, query, max(1, min(int(limit), 100)))}


def legacy_reconciliation_loop(self):
    """run_identity_reconciliation_loop before the change: status() ran inside the service lock."""
    while not self._stop.is_set():
        try:
            before = self.identity_web.revision()
            worked = self.identity_web.process_one()
            if self.identity_web.revision() != before:
                self._identity_wakeup.set()
            with self.lock:
                self.identity_progress.update(self.identity_web.status(), loop_heartbeat_at=identity_now())
                self.metadata_epoch += 1
                self.bytes_cache.clear()
            if self._stop.wait(3 if worked else 30):
                break
        except Exception as exc:
            with self.lock:
                self.identity_progress.update(status='reference_check_attention', errors=[str(exc)], loop_heartbeat_at=identity_now())
            if self._stop.wait(30):
                break


def legacy_snapshot(service, query='', limit=700, since=None):
    """LiveService._snapshot before the live fields were captured with the view: the view was taken first, the projection ran,
    and only then were the metadata, identity counts, inventory, evidence sources and errors read from the service."""
    started = time.perf_counter()
    if not service.ready or service._assertion_update_pending:
        return {'schema': 'melange-staffer-live-v2', 'ready': False, 'stage': service.stage}
    view = service._current_view()
    graph = service._graph(query, limit, view)
    with service.lock:
        metadata, current = service.metadata, service._current_metadata()
        result = {**graph, **current, 'schema': 'melange-staffer-live-v2', 'ready': True, 'stage': service.stage,
                  'source_revision': view.revision,
                  **service._completion_state(view.static), 'generated_at': service_module.utcnow(),
                  'source_coverage': deepcopy(service.inventory.get('sourcecoverage', {})),
                  'identity_aliases': view.aliases,
                  'identity_review': service.identity_resolution.get('counts', {}),
                  'identity_resolution': deepcopy(service.identity_progress),
                  'identity_review_errors': service.identity_resolution.get('errors', []),
                  'inventory': {'counts': service.inventory.get('counts', {}),
                      'headcount_semantics': service.inventory.get('headcount_semantics'),
                      'searchable_person_nodes': service.metrics.get('searchable_person_nodes')},
                  'errors': graph.get('errors', []) + service.errors + metadata.get('errors', [])
                            + ['Identity review: ' + str(e) for e in service.identity_resolution.get('errors', [])],
                  'sources': view.static.sources + service.evidence.get('sources', []) + service.inventory.get('sources', []) + [{'label': 'Politico crawl verification',
                      'path': str(service.profiles.raw_dir / 'crawl_verification.json'), 'sha256': service.profiles.accepted_receipt_digest}],
                  'people_only_notice': graph.get('notice')}
        result['metadata_revision'] = service_module.digest([metadata, service.errors, service.stage,
                                                             service.identity_progress.get('politico_ingestion'), current.get('freshness', {}).get('stale')])[:16]
        result['unchanged'] = since == result['revision']
        service.metrics['snapshot_ms'] = round((time.perf_counter() - started) * 1000, 2)
        result['performance'] = dict(service.metrics)
    return result


def timed(call):
    started = time.perf_counter()
    result = call()
    return result, time.perf_counter() - started


def blocked(call, hold):
    """True when `call`, started on another thread while the lock is held, has not finished after PROMPT."""
    done = threading.Event()
    thread = threading.Thread(target=lambda: (call(), done.set()), daemon=True)
    thread.start()
    finished_early = done.wait(PROMPT)
    return not finished_early, thread


# ------------------------------------------------------- 1. nothing waits for the service lock

QUERIES = ['ada', 'al', 'a', 'co', 'turing', 'gordon holz', 'politico:u1', 'zzzz']


def test_search_and_offices_answer_promptly_while_another_thread_holds_the_service_lock():
    service, _ = memo_service()
    with LockHeld(service, 3.0):
        for query in QUERIES:
            result, elapsed = timed(lambda: service.search(query, 20))
            assert result['ready'] and elapsed < PROMPT, (query, elapsed)
        for query in ('test', 'of', 'x'):
            result, elapsed = timed(lambda: service.offices(query, 20))
            assert result['ready'] and elapsed < PROMPT, (query, elapsed)
        # An uncached projection of the map is not blocked either; the snapshot only needs the lock for its short copy.
        graph, elapsed = timed(lambda: service._graph('Gordon', 20))
        assert graph['nodes'] and elapsed < PROMPT


def test_negative_control_the_pre_change_search_and_offices_wait_for_the_lock():
    service, _ = memo_service()
    with LockHeld(service, 1.0) as held:
        search_blocked, search_thread = blocked(lambda: legacy_search(service, 'ada', 20), 1.0)
        offices_blocked, offices_thread = blocked(lambda: legacy_offices(service, 'test', 20), 1.0)
        assert search_blocked and offices_blocked, 'the harness must see the old code wait for the held lock'
        held.release.set()
        search_thread.join(5)
        offices_thread.join(5)
    assert not search_thread.is_alive() and not offices_thread.is_alive()


def test_the_new_answers_equal_the_pre_change_answers_through_the_service():
    service, _ = memo_service()
    for query in QUERIES + ['  Ada  ', 'ADA LOVELACE', 'person:gordon', 'lda:lobbyist:1', 'member:A000370']:
        for limit in (1, 5, 20, 100):
            service.search_cache.clear()
            assert service.search(query, limit) == legacy_search(service, query, limit), (query, limit)
    for query in ('test', 'of', 'office', 'x', ''):
        assert service.offices(query, 20) == legacy_offices(service, query, 20)


def test_an_unready_service_answers_not_ready_without_a_lock():
    service, _ = memo_service()
    service.ready, service.stage = False, 'Resolving'
    with LockHeld(service, 3.0):
        assert service.search('ada') == {'nodes': [], 'ready': False, 'stage': 'Resolving'}
        assert service.offices('test') == {'ready': False, 'nodes': [], 'stage': 'Resolving'}
    service.ready, service._assertion_update_pending = True, True
    assert service.search('ada')['ready'] is False
    assert service.offices('test')['ready'] is False


def test_the_search_cache_has_its_own_lock_and_the_same_key():
    service, _ = memo_service()
    first = service.search('ada', 20)
    assert service.search('ada', 20) is first
    assert (service.revision, 'ada', 20) in service.search_cache
    assert service.search('ada', 5) is not first, 'the limit is part of the key'
    with LockHeld(service, 3.0):
        assert service.search('ada', 20) is first
    assert service._search_lock is not service.lock
    with service._search_lock:        # a reader holding only the cache lock does not stop the service lock either
        with LockHeld(service, 0.2):
            pass
    service._refresh_profiles(force=True)
    assert len(service.search_cache) == 0, 'a new revision empties the cache'


# ---------------------------------------------------- 2. one view per answer: no torn reads

def generation(tag):
    """A complete state whose revision, router, profile people and offices all carry `tag`."""
    nodes = {}
    for number in range(12):
        node = profile_node(f'{tag.lower()}{number}', f'{tag} Person {number}')
        nodes[node['id']] = node
    search = [(n['id'], ' '.join([n['id'], n['label']]).casefold()) for n in nodes.values()]
    office = {'id': f'office:{tag}', 'kind': 'office', 'label': f'{tag} Office', 'staff_count': 1, 'records_through': '2026-01-01',
              'roster_basis': 'Latest payroll records', 'roster': {}}
    router = SimpleNamespace(revision=f'rev-{tag}', as_of='2026-01-01', nodes={}, office_index={office['id']: office},
                             person_facts=SimpleNamespace(network_excluded=frozenset()))
    return {'revision': f'rev-{tag}', 'router': router, 'profile_nodes': nodes, 'profile_search': search}


def tag_of(label):
    return label.split()[0]


def one_generation(answer):
    """An answer is whole when everything in it carries one tag and that tag's revision."""
    tags = {tag_of(node['label']) for node in answer['nodes']}
    return len(tags) == 1 and answer['revision'] == 'rev-' + next(iter(tags))


@pytest.fixture
def fast_thread_switching():
    """Switch threads far more often than the default 5 ms, so readers and the publisher interleave closely."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    yield
    sys.setswitchinterval(previous)


def run_readers(read, publish, ready, deadline=30.0, readers=4, until_bad=False):
    """Run readers against a publisher until `ready()` (or, with until_bad, until a reader sees something wrong), at most `deadline` seconds."""
    stop, bad, checks, seen = threading.Event(), [], [0], set()

    def reader():
        while not stop.is_set():
            outcome = read()
            checks[0] += 1
            seen.add(outcome[0])
            if not outcome[1]:
                bad.append(outcome)
                if until_bad:
                    stop.set()
    threads = [threading.Thread(target=reader, daemon=True) for _ in range(readers)] + [threading.Thread(target=publish, args=(stop,), daemon=True)]
    for thread in threads:
        thread.start()
    end = time.monotonic() + deadline
    while not stop.is_set() and time.monotonic() < end and not ready():
        time.sleep(0.005)
    stop.set()
    for thread in threads:
        thread.join(5)
    return bad, checks[0], seen


def test_every_answer_comes_from_one_view_while_views_are_swapped_underneath(fast_thread_switching):
    service, _ = memo_service()
    views = [service._compose_view(**generation(tag)) for tag in ('Alpha', 'Beta')]
    flips = [0]

    def publish(stop):
        for view in itertools.cycle(views):
            if stop.is_set():
                return
            service._view = view          # the one assignment that publishes a generation
            with service._search_lock:
                service.search_cache.clear()
            flips[0] += 1
            time.sleep(0.0002)

    checks_seen = [0]

    def read():
        # Two separate calls may be answered from two generations; each answer must be whole.
        search, offices = service.search('person', 20), service.offices('office', 20)
        checks_seen[0] += 1
        return (frozenset(tag_of(node['label']) for node in search['nodes'] + offices['nodes']),
                one_generation(search) and one_generation(offices))
    service._view = views[0]
    bad, checks, seen = run_readers(read, publish, ready=lambda: flips[0] >= 300 and checks_seen[0] >= 300)
    assert not bad, bad[:3]
    assert flips[0] >= 300, 'the publisher swapped views %d times while %d answers were checked' % (flips[0], checks)
    assert {'Alpha', 'Beta'} <= {tag for tags in seen for tag in tags}, 'readers saw both generations'


def test_negative_control_reading_the_service_attributes_one_by_one_does_see_torn_states(fast_thread_switching):
    service, _ = memo_service()
    states = [generation(tag) for tag in ('Alpha', 'Beta')]

    def publish(stop):
        for state in itertools.cycle(states):   # the pre-change way: each attribute is assigned on its own
            if stop.is_set():
                return
            service.revision = state['revision']
            time.sleep(0)
            service.profile_nodes = state['profile_nodes']
            time.sleep(0)

    def read():
        revision, nodes = service.revision, service.profile_nodes
        tags = {tag_of(node['label']) for node in nodes.values() if node['label'].startswith(('Alpha', 'Beta'))}
        ok = not tags or (len(tags) == 1 and revision == 'rev-' + next(iter(tags)))
        return (frozenset(tags), ok)
    bad, checks, _ = run_readers(read, publish, ready=lambda: False, deadline=20.0, until_bad=True)
    assert bad, 'without a single published view the harness catches a revision paired with another generation\'s people (%d checks)' % checks


def test_a_view_is_immutable_and_complete():
    service, _ = memo_service()
    view = service._view
    assert dataclasses.is_dataclass(view)
    for name in ('revision', 'static', 'profile_nodes', 'profile_search', 'aliases', 'activity_provider', 'router',
                 'office_index', 'excluded', 'index'):
        assert hasattr(view, name), name
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.revision = 'other'
    assert not hasattr(view, '__dict__'), 'slots: no attribute can be added either'
    assert view.revision == service.revision and view.router is service.router and view.profile_nodes is service.profile_nodes
    assert view.profile_search is service.profile_search and view.aliases is service.identity_aliases and view.static is service.static
    assert view.office_index is service.router.office_index and view.index.ok


def test_a_service_that_never_published_a_view_composes_one_on_first_read():
    service, _ = memo_service()
    service._view = None
    assert 'Ada Lovelace' in [node['label'] for node in service.search('ada', 20)['nodes']]
    assert service._view is not None and service._view.revision == service.revision
    service._view = None
    assert service.offices('test')['nodes']


# ---------------------------------------------------- 3. the map and the snapshot

def test_the_projection_runs_without_the_service_lock_and_the_snapshot_locks_only_to_copy(monkeypatch):
    service, _ = memo_service()
    seen = []
    original = service_module.project_people

    def spy(*args, **kwargs):
        seen.append(service.lock._is_owned())
        return original(*args, **kwargs)
    monkeypatch.setattr(service_module, 'project_people', spy)
    snapshot = service.snapshot('Gordon', 20)
    assert seen == [False], 'the slow projection must not hold the service lock'
    assert snapshot['ready'] and snapshot['nodes']
    # Held for the short copy only: a second thread can take the lock again straight after the call returns.
    assert service.lock.acquire(timeout=0.5)
    service.lock.release()


def test_a_snapshot_describes_one_view_even_when_a_refresh_swaps_while_it_projects(monkeypatch):
    service, provider = memo_service()
    old_revision, old_as_of = service.revision, service.router.as_of
    inside, release = threading.Event(), threading.Event()
    original = service_module.project_people

    def slow(*args, **kwargs):
        inside.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(service_module, 'project_people', slow)
    out = {}
    worker = threading.Thread(target=lambda: out.update(snapshot=service.snapshot('Gordon', 20)), daemon=True)
    worker.start()
    assert inside.wait(5)
    with pytest.MonkeyPatch.context() as other:
        other.setattr(service_module, 'local_date', lambda: '2026-09-30')
        started = time.perf_counter()
        service._refresh_profiles(force=True)     # the old code would wait here for the snapshot to finish
        assert time.perf_counter() - started < 5
    assert service.revision != old_revision
    release.set()
    worker.join(10)
    snapshot = out['snapshot']
    assert snapshot['source_revision'] == old_revision, 'one view, whole: the snapshot is of the state it started from'
    assert snapshot['routing']['as_of'] == old_as_of != service.router.as_of


# What a snapshot reports from the live service rather than from its view: every one of these must come from the moment
# the view was taken.
LIVE_FIELDS = ('source_revision', 'cycle', 'stage', 'status', 'source_coverage', 'identity_review', 'identity_resolution',
               'identity_review_errors', 'inventory', 'errors', 'sources', 'metadata_revision')


def snapshot_during_a_refresh(monkeypatch, take):
    """Hold `take(service)` inside the projection while a refresh publishes a new revision and other threads move the
    live fields on. Returns what it answered, a quiet snapshot of the service before, and a quiet one after."""
    service, provider = memo_service()
    quiet = service.snapshot('Gordon', 20)
    service._clear_view_caches()          # the held snapshot must really project, not read the quiet one's cached map
    inside, release = threading.Event(), threading.Event()
    original = service_module.project_people

    def slow(*args, **kwargs):
        inside.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(service_module, 'project_people', slow)
    out = {}
    worker = threading.Thread(target=lambda: out.update(snapshot=take(service)), daemon=True)
    worker.start()
    assert inside.wait(5)
    provider.profile_revision = 'rev-new'
    service._refresh_profiles()           # a new accepted snapshot: new revision, router, view and poll metadata
    with service.lock:                    # and what the identity loop and the refresh update in place
        service.stage = 'Reconciling identities'
        service.errors.append('a later error')
        service.identity_resolution = {'counts': {'pairs': 99}, 'errors': ['a later identity error']}
        service.identity_progress['status'] = 'a later status'
        service.inventory['counts'] = {'people': 99}
        service.inventory['sources'] = [{'label': 'a later inventory source'}]
        service.inventory.setdefault('sourcecoverage', {}).setdefault('politico', {})['validated_profiles'] = 12345
        service.evidence['sources'] = [{'label': 'a later evidence source'}]
    release.set()
    worker.join(10)
    assert not worker.is_alive()
    return out['snapshot'], quiet, service.snapshot('Gordon', 20)


def test_a_snapshot_reports_the_live_fields_as_they_were_when_its_view_was_taken(monkeypatch):
    snapshot, quiet, later = snapshot_during_a_refresh(monkeypatch, lambda service: service.snapshot('Gordon', 20))
    for field in LIVE_FIELDS:
        assert later[field] != quiet[field], 'the harness moved %s, so the answer can tell the two moments apart' % field
    assert later['source_revision'] != quiet['source_revision']
    for field in LIVE_FIELDS:
        assert snapshot[field] == quiet[field], '%s must come from the same moment as the view' % field
    assert snapshot['routing']['as_of'] == quiet['routing']['as_of']
    assert snapshot['nodes'] == quiet['nodes'] and snapshot['revision'] == quiet['revision']


def test_negative_control_reading_the_live_fields_after_the_projection_mixes_two_generations(monkeypatch):
    snapshot, quiet, later = snapshot_during_a_refresh(monkeypatch, lambda service: legacy_snapshot(service, 'Gordon', 20))
    assert snapshot['source_revision'] == quiet['source_revision'], 'the projected view is the old one'
    torn = {field for field in LIVE_FIELDS if snapshot[field] != quiet[field]}
    assert torn == set(LIVE_FIELDS) - {'source_revision'}, 'the harness must see every live field come from the later moment'
    for field in torn:
        assert snapshot[field] == later[field], field


class CountingLock:
    """The service lock, counting how many times it is taken."""

    def __init__(self, inner):
        self.inner, self.holds = inner, 0

    def __enter__(self):
        self.inner.acquire()
        self.holds += 1
        return self

    def __exit__(self, *exc):
        self.inner.release()

    def _is_owned(self):
        return self.inner._is_owned()


def test_the_view_and_the_live_fields_are_taken_in_one_hold_of_the_lock_and_the_projection_runs_outside_it(monkeypatch):
    service, _ = memo_service()
    service.lock = CountingLock(service.lock)
    seen = []
    for name in ('_current_view', '_snapshot_state'):
        original = getattr(service, name)

        def spy(*args, _original=original, _name=name, **kwargs):
            seen.append((_name, service.lock._is_owned(), service.lock.holds))
            return _original(*args, **kwargs)
        monkeypatch.setattr(service, name, spy)
    projected = service_module.project_people

    def project_spy(*args, **kwargs):
        seen.append(('project_people', service.lock._is_owned(), service.lock.holds))
        return projected(*args, **kwargs)
    monkeypatch.setattr(service_module, 'project_people', project_spy)
    before = service.lock.holds
    assert service.snapshot('Gordon', 20)['ready']
    assert [name for name, _, _ in seen] == ['_current_view', '_snapshot_state', 'project_people']
    (_, view_owned, view_hold), (_, state_owned, state_hold), (_, project_owned, _) = seen
    assert view_owned and state_owned and view_hold == state_hold == before + 1, 'one hold takes the view and the fields'
    assert not project_owned, 'the slow projection holds no lock'
    assert service.lock.holds == before + 1, 'and nothing else in the snapshot takes the service lock'


# ---------------------------------------------------- 4. the reconciliation loop

def run_loop(loop, service, hold_status=None):
    """Run one pass of a reconciliation loop whose status() blocks until released; report whether the lock was free meanwhile."""
    inside, release = threading.Event(), threading.Event()

    class Web:
        def revision(self):
            return 'r1'

        def process_one(self):
            return False

        def status(self):
            inside.set()
            release.wait(5)
            service._stop.set()
            return {'status': 'checking_references', 'queued': 1}
    service.identity_web = Web()
    thread = threading.Thread(target=lambda: loop(service), daemon=True)
    thread.start()
    assert inside.wait(5)
    free = service.lock.acquire(timeout=0.5)
    if free:
        service.lock.release()
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    return free


def test_the_loop_reads_the_reference_status_before_it_takes_the_service_lock():
    service, _ = memo_service()
    epoch = service.metadata_epoch
    service.bytes_cache['sentinel'] = 1
    assert run_loop(service_module.LiveService.run_identity_reconciliation_loop, service) is True
    progress = service.identity_progress
    assert progress['status'] == 'checking_references' and progress['queued'] == 1
    assert progress['loop_heartbeat_at'].startswith('20')
    assert service.metadata_epoch == epoch + 1, 'the epoch is bumped'
    assert 'sentinel' not in service.bytes_cache, 'and the byte cache is cleared'


def test_negative_control_the_pre_change_loop_holds_the_lock_while_it_reads_the_status():
    service, _ = memo_service()
    assert run_loop(legacy_reconciliation_loop, service) is False
    assert service.identity_progress['status'] == 'checking_references', 'the old loop still finished its pass'

def test_the_loop_reads_the_reference_status_once_per_interval_but_beats_every_pass(monkeypatch):
    """status() scans tables that grow with every check (~1 s of CPU at 933k rows); read after every ~7 s lookup it was most
    of the sidecar's idle CPU. It is read on the first pass, when a reference changes, and then once per interval."""
    service, _ = memo_service()
    calls, passes, revisions = [], [], iter(['r1', 'r1', 'r1', 'r1', 'r1', 'r2'] + ['r2'] * 10)
    monkeypatch.setattr(service._stop, 'wait', lambda seconds: len(passes) >= 3)

    class Web:
        def revision(self):
            return next(revisions)

        def process_one(self):
            passes.append(1)
            return True

        def status(self):
            calls.append(len(passes))
            return {'status': 'checking_references', 'queued': len(passes)}
    service.identity_web = Web()
    service.run_identity_reconciliation_loop()
    assert passes == [1, 1, 1] and calls == [1, 3], 'first pass, skipped pass, then the pass whose revision changed'
    assert service.identity_progress['queued'] == 3 and service.identity_progress['loop_heartbeat_at'].startswith('20')
