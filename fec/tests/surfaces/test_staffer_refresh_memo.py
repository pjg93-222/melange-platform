"""The 30-second refresh does not rebuild what its inputs did not change, and it does not hold the service lock while it builds.

A reader that names its accepted snapshot (content revision, prefix hash, receipt digest) lets an unchanged cycle keep the
structures the previous cycle published. Every integrity check still runs on every cycle. Any input that changes, or a
forced refresh, rebuilds.
"""
import copy
from types import SimpleNamespace

import pytest

from fec.staffer_network.member_context import MemberContext
from fec.surfaces import staffer_live_server as server_module
from fec.surfaces import staffer_live_service as service_module
from fec.surfaces.staffer_live_server import IncrementalProfiles
from fec.surfaces.staffer_live_service import ProfileSearchIndex, routing_signature
from fec.tests.surfaces.staffer_latency_fixtures import MemoProfiles, memo_service, office_graph
from fec.tests.surfaces.test_staffer_live_server import checkpoint, profile, write_rows


def published(service):
    return (service.profile_nodes, service.profile_edges, service.profile_by_person, service.profile_search,
            service.router.nodes, service._view, service.router, service.router.nodes_revision)


def same_objects(before, after):
    return all(a is b or (isinstance(a, int) and a == b) for a, b in zip(before, after))


def settle(service, limit=4):
    """Poll until a cycle reuses what the previous one built; returns whether it did."""
    for _ in range(limit):
        service._refresh_profiles()
        if service.metrics['profile_refresh_reused']:
            return True
    return False


# ----------------------------------------------------------------------------- unchanged inputs

def test_an_unchanged_poll_rebuilds_nothing_but_still_publishes_what_a_poll_publishes():
    service, provider = memo_service(settle=False)
    assert service._refresh_memo is not None, 'the refresh that built the state recorded it'
    before, graphs, refreshes, epoch = published(service), provider.graphs, provider.refreshes, service.metadata_epoch
    service.bytes_cache['sentinel'] = 1
    for cycle in range(1, 4):
        service._refresh_profiles()
        assert provider.graphs == graphs, 'graph() is not called again'
        assert provider.refreshes == refreshes + cycle, 'but the reader still refreshes, and with it runs its integrity checks'
        assert same_objects(before, published(service)), 'nothing the service serves is rebuilt or replaced'
        assert service.metrics['profile_refresh_reused'] is True
        assert service.metadata is provider.metadata and service.metadata['cycle'] == provider.refreshes, 'the metadata is published'
        assert service.metadata_epoch == epoch + cycle, 'the epoch is bumped'
        assert 'sentinel' not in service.bytes_cache, 'and the byte cache is cleared'
        service.bytes_cache['sentinel'] = 1
    assert service.router.nodes_revision == 0


def test_a_refresh_that_changed_the_router_records_what_it_built_so_the_very_next_poll_reuses():
    service, provider = memo_service(settle=False)
    assert provider.graphs == 1 and service._refresh_memo is not None
    router, nodes = service.router, service.router.nodes
    service._refresh_profiles()
    assert provider.graphs == 1 and service.metrics['profile_refresh_reused'] is True
    assert service.router is router and service.router.nodes is nodes, 'router.nodes is not replaced when the key is unchanged'


def test_a_reader_that_does_not_name_its_snapshot_is_never_memoised():
    from fec.surfaces.test_staffer_live_service import ProfilesFixture
    service, provider = memo_service(provider=ProfilesFixture())
    graphs = []
    original = provider.graph
    provider.graph = lambda *args: graphs.append(1) or original(*args)
    for _ in range(3):
        service._refresh_profiles()
    assert len(graphs) == 3 and service._refresh_memo is None and service.metrics['profile_refresh_reused'] is False


# ----------------------------------------------------------------------------- every key input

def change_profile_revision(service, provider, patch):
    provider.profile_revision = 'rev-2'


def change_prefix(service, provider, patch):
    provider.accepted_prefix_sha256 = 'prefix-2'


def change_receipt(service, provider, patch):
    provider.accepted_receipt_digest = 'receipt-2'


def change_static_graph(service, provider, patch):
    service.static = office_graph()


def change_pending_assertions(service, provider, patch):
    service.static.pending_source_assertions = {}


def change_aliases(service, provider, patch):
    service.identity_aliases = {}


def change_activity_provider(service, provider, patch):
    service.activity_provider = SimpleNamespace(to_node={}, revision=None, receipt=None)


def change_contacts(service, provider, patch):
    service.contacts = copy.copy(service.contacts)


def change_assertions(service, provider, patch):
    patch.setattr(service_module, 'load_coworker_assertions',
                  lambda: [{'person_ids': ['person:gordon', 'person:stephanie'], 'as_of': '2026-09-04', 'provenance': {'source': 'fixture'}}])


def change_member_context(service, provider, patch):
    import fec.staffer_network.member_context as module
    patch.setattr(module, 'load_member_context', lambda root: MemberContext((), {'status': 'available', 'sha256': 'another'}))


def change_as_of(service, provider, patch):
    patch.setattr(service_module, 'local_date', lambda: '2026-09-30')


# (name, change, the router is replaced too, the rebuild is counted on the replacement reader)
CHANGES = [
    ('profile revision', change_profile_revision, True),
    ('prefix hash', change_prefix, False),
    ('receipt digest', change_receipt, False),
    ('static graph', change_static_graph, False),
    ('pending source assertions', change_pending_assertions, False),
    ('identity aliases', change_aliases, False),
    ('activity provider', change_activity_provider, False),
    ('contacts', change_contacts, False),
    ('assertion revision', change_assertions, True),
    ('member-context revision', change_member_context, True),
    ('as_of', change_as_of, True),
]


@pytest.mark.parametrize('name,change,replaces_router', CHANGES, ids=[c[0] for c in CHANGES])
def test_a_change_to_any_key_input_rebuilds(name, change, replaces_router):
    service, provider = memo_service()
    assert settle(service) and service.metrics['profile_refresh_reused']
    router, view, profile_nodes, graphs = service.router, service._view, service.profile_nodes, provider.graphs
    with pytest.MonkeyPatch.context() as patch:
        change(service, provider, patch)
        service._refresh_profiles()
        assert provider.graphs == graphs + 1, name + ': the graph is built again'
        assert service.profile_nodes is not profile_nodes and service._view is not view, name + ': new structures were published'
        assert service.metrics['profile_refresh_reused'] is False
        assert (service.router is not router) is replaces_router, name
        # and the rebuilt state is recorded again: a later unchanged cycle reuses it
        assert settle(service), name
        graphs = provider.graphs
        service._refresh_profiles()
        assert provider.graphs == graphs and service.metrics['profile_refresh_reused'] is True


def test_contacts_are_attached_through_get_by_the_first_refresh_as_the_second_poll_always_did():
    """ContactIndex.bind attaches contacts by node id; get() also accepts the person: spelling of a source id, and it is the
    state every poll after the first one left the nodes in. The refresh that now records the memo leaves them in that state."""
    from fec.surfaces.staffer_contacts import ContactIndex
    graph = office_graph()
    ident = 'person:lda:lobbyist:154321'
    graph.nodes[ident] = {'id': ident, 'kind': 'person', 'label': 'Kathleen Coulombe', 'member_ids': [], 'committee_ids': [],
                          'policy_ids': [], 'stints': [], 'aliases': []}
    graph.reindex()
    from fec.surfaces.staffer_live_service import LiveService
    service = LiveService('fixture', graph)
    service.contacts = ContactIndex(records=[{'source_id': 'lda:lobbyist:154321', 'name': 'Kathleen Coulombe', 'office': 'Rep. Test',
                                              'title': 'Counsel', 'contacts': [{'kind': 'email', 'value': 'kc@example.org', 'label': 'Work',
                                                                                'provenance': {'source': 'fixture'}}]}])
    service.profiles = MemoProfiles()
    service._refresh_profiles(force=True)
    first = service.router.nodes[ident]['contacts']
    assert [c['value'] for c in first] == ['kc@example.org'] and first == service.contacts.get(ident)
    assert service.contacts.by_person.get(ident) is None, 'bind alone would have left this node without them'
    service._refresh_profiles()
    assert service.metrics['profile_refresh_reused'] is True and service.router.nodes[ident]['contacts'] == first
    service.profiles.accepted_prefix_sha256 = 'prefix-2'     # and a rebuild of the profile structures leaves the same state
    service._refresh_profiles()
    assert service.router.nodes[ident]['contacts'] == first


def test_the_first_poll_after_an_identity_swap_rebuilds_with_the_services_own_contacts():
    service, provider = memo_service()
    service._refresh_memo = None                           # what the swap in _rebuild_identity leaves behind
    graphs = provider.graphs
    service._refresh_profiles()
    assert provider.graphs == graphs + 1 and service.metrics['profile_refresh_reused'] is False
    assert service._refresh_memo is not None
    service._refresh_profiles()
    assert service.metrics['profile_refresh_reused'] is True


def test_a_replaced_profile_reader_rebuilds_from_the_new_reader():
    service, provider = memo_service()
    replacement = MemoProfiles(labels=('Zed Zimmer', 'Yan Young'))
    service.profiles = replacement
    service._refresh_profiles()
    assert replacement.graphs == 1 and service.metrics['profile_refresh_reused'] is False
    assert sorted(n['label'] for n in service.profile_nodes.values() if n['kind'] == 'person') == ['Yan Young', 'Zed Zimmer']


def test_a_forced_refresh_always_rebuilds():
    service, provider = memo_service()
    graphs, router, memo = provider.graphs, service.router, service._refresh_memo
    service._refresh_profiles(force=True)
    assert provider.graphs == graphs + 1 and service.router is not router
    assert service._refresh_memo is not None and service._refresh_memo is not memo, 'and it records what it built afresh'


def test_an_identity_rebuild_swap_forgets_the_memo_and_publishes_the_replacements_view():
    service, provider = memo_service()
    working = type(service)(service.source_root, service.static)
    working.profiles, working.profile_lock = provider, service.profile_lock
    working.contacts = service.contacts
    working._refresh_profiles(force=True)
    assert service._refresh_memo is not None
    swapped = ('static', 'evidence', 'inventory', 'identity_aliases', 'activity_provider', 'router', 'profile_nodes', 'profile_edges',
               'profile_by_person', 'profile_search', 'revision', '_profile_revision', '_assertion_revision', 'metadata')
    with service.lock:
        for field in swapped:
            setattr(service, field, getattr(working, field))
        service._view, service._refresh_memo = working._view, None
    graphs = provider.graphs
    service._refresh_profiles()
    assert provider.graphs == graphs + 1, 'the first poll after a swap rebuilds'
    assert settle(service)


# ----------------------------------------------------------------------------- integrity checks

def test_integrity_checks_run_on_every_cycle_including_reused_ones(monkeypatch):
    service, provider = memo_service()
    calls = []
    original = service._check_review_assertion_coverage
    monkeypatch.setattr(service, '_check_review_assertion_coverage', lambda nodes: calls.append(len(nodes)) or original(nodes))
    refreshes = provider.refreshes
    for _ in range(3):
        service._refresh_profiles()
    assert service.metrics['profile_refresh_reused'] is True
    assert provider.refreshes == refreshes + 3 and len(calls) == 3 and all(count > 0 for count in calls)


def test_review_assertion_coverage_still_fails_a_reused_cycle_and_publishes_nothing():
    service, provider = memo_service()
    assert settle(service)
    epoch, metadata, nodes = service.metadata_epoch, service.metadata, service.profile_nodes
    service.static.receipt = {'files': [{'role': 'staffer_social_politico_review_layers', 'records': 7}]}
    with pytest.raises(ValueError, match='Review assertion coverage mismatch: 0 served / 7 declared'):
        service._refresh_profiles()
    assert service.metadata_epoch == epoch and service.metadata is metadata and service.profile_nodes is nodes
    service.static.receipt = {}
    service._refresh_profiles()
    assert service.metadata_epoch == epoch + 1


def test_the_receipt_and_uuid_checks_run_on_a_cycle_whose_derived_digests_are_reused(tmp_path):
    write_rows(tmp_path / 'profiles.ndjson', [profile('one'), profile('two')])
    checkpoint(tmp_path, 2)
    reader = IncrementalProfiles(tmp_path)
    reader.refresh()
    derived = (reader.accepted_prefix_sha256, reader.profile_revision, reader.profile_contracts)
    reader.refresh()
    assert reader.errors == [] and (reader.accepted_prefix_sha256, reader.profile_revision) == derived[:2]
    assert reader.profile_contracts is derived[2], 'the derived contracts were reused'
    # A row damaged in memory: the UUID/URL check runs on every cycle, derived values or not.
    reader.rows[0]['source_url'] = 'https://directory.example/other'
    result = reader.refresh()
    assert any('Profile UUID and provenance URL disagree' in error for error in result['errors'])
    assert result['crawl']['last_good_preserved'] and reader.accepted_prefix_sha256 == derived[0]
    reader.rows[0]['source_url'] = profile('one')['source_url']
    assert reader.refresh()['errors'] == []
    # A receipt that stops passing is noticed on a cycle with an unchanged key, too.
    checkpoint(tmp_path, 2, integrity_status='FAIL')
    result = reader.refresh()
    assert result['errors'] and result['crawl']['last_good_preserved'] and reader.accepted_prefix_sha256 == derived[0]


# ------------------------------------------------- the derived digests of the reader: same values, less work

STATE = ('accepted_prefix_sha256', 'accepted_receipt_digest', 'profile_digests', 'profile_contracts', 'eligible_profile_digests',
         'profile_revision', 'exceptions', 'profile_changes', 'generation', 'verified_byte_binding', 'accepted')


def observed(reader):
    return {name: copy.deepcopy(getattr(reader, name)) for name in STATE} | {'summary': reader.summary(), 'errors': list(reader.errors)}


def test_the_reused_derived_values_equal_what_a_full_derivation_gives_on_every_cycle(tmp_path, monkeypatch):
    # Two readers over the same files: `fast` is the code as it is, `slow` is made to derive everything on every cycle.
    crawl = tmp_path
    write_rows(crawl / 'profiles.ndjson', [profile('one'), profile('two'), profile('three', verified_name='')])
    checkpoint(crawl, 3)
    fast, slow = IncrementalProfiles(crawl), IncrementalProfiles(crawl)
    digests = []
    original = server_module.digest
    monkeypatch.setattr(server_module, 'digest', lambda value: digests.append(1) or original(value))

    def both(step, fast_digests=None):
        before = len(digests)
        fast.refresh()
        fast_count = len(digests) - before
        slow._derived_key = None          # the behaviour before the change: every cycle derives everything
        slow.refresh()
        a, b = observed(fast), observed(slow)
        a['summary']['freshness'] = b['summary']['freshness'] = None   # the clock, not the derivation
        assert a == b, step
        return fast_count

    first_cycle = both('first')
    assert first_cycle > 0
    assert both('unchanged 1') < first_cycle // 2, 'the unchanged cycle did not derive the digests again'
    assert both('unchanged 2') < first_cycle // 2
    write_rows(crawl / 'profiles.ndjson', [profile('four')], append=True)
    checkpoint(crawl, 4)
    assert both('one row added') > 0
    assert observed(fast)['profile_changes']['added'] == ['four']
    both('unchanged 3')
    checkpoint(crawl, 4, generated_at='2026-09-05T00:00:00Z')        # a new receipt for the same prefix
    assert both('new receipt, same rows') > 0
    checkpoint(crawl, 4, integrity_status='FAIL')                    # a failing receipt keeps the last good state
    both('failing receipt')
    both('failing receipt again')
    assert fast.errors and slow.errors
    checkpoint(crawl, 4, generated_at='2026-09-05T00:00:00Z')        # and the good receipt again is the same state as before
    both('recovered')
    assert not fast.errors and observed(fast)['profile_changes']['unchanged_count'] == 4


# ----------------------------------------------- built before the lock, released after it

def test_what_the_refresh_builds_is_built_outside_the_service_lock(monkeypatch):
    service, provider = memo_service()
    owned = []
    original_signature, original_get = service_module.routing_signature, service.contacts.get
    monkeypatch.setattr(service_module, 'routing_signature', lambda nodes: owned.append(('signature', service.lock._is_owned())) or original_signature(nodes))
    monkeypatch.setattr(service.contacts, 'get', lambda ident: owned.append(('contacts', service.lock._is_owned())) or original_get(ident), raising=False)

    class Spy(ProfileSearchIndex):
        def __init__(self, *args):
            owned.append(('index', service.lock._is_owned()))
            super().__init__(*args)
    monkeypatch.setattr(service_module, 'ProfileSearchIndex', Spy)
    provider.accepted_prefix_sha256 = 'prefix-2'          # a rebuild without a new router: contacts, signature, view
    service._refresh_profiles()
    provider.profile_revision = 'rev-2'                    # a new router: signature of the new nodes, the view
    service._refresh_profiles()
    kinds = {kind for kind, _ in owned}
    assert {'signature', 'contacts', 'index'} <= kinds, owned[:5]
    assert not [entry for entry in owned if entry[1]], 'built while holding the service lock: ' + repr([e for e in owned if e[1]][:3])
    with service.lock:                                       # the detector does see the lock when it is held
        assert service.lock._is_owned()


def test_replaced_structures_are_released_after_the_lock_is_dropped(monkeypatch):
    deleted = []

    class Tracked(ProfileSearchIndex):
        def __del__(self):
            deleted.append(service.lock._is_owned())
    monkeypatch.setattr(service_module, 'ProfileSearchIndex', Tracked)
    service, provider = memo_service(settle=False)       # built: the first index
    provider.accepted_prefix_sha256 = 'prefix-2'         # the snapshot moved: rebuilt, the router kept, the old view retired
    service._refresh_profiles()
    assert deleted == [False], 'the superseded view and its index are freed after the lock is released'
    service._refresh_profiles(force=True)                # a changed refresh replaces it again
    assert deleted == [False, False]


# ----------------------------------------------------------------------------- the full-network cache

def test_the_full_network_cache_survives_polls_and_is_dropped_when_the_revision_moves():
    service, provider = memo_service()
    first = service.full_network()
    assert service._full_network_payload is not None
    for _ in range(3):
        service._refresh_profiles()
        assert service._full_network_payload is not None and service._full_network_revision == (service._profile_revision, service.revision), \
            'nothing changed, so the cached network stays'
    again = service.full_network()
    assert again['nodes'] == first['nodes'] and again['edges'] == first['edges']
    provider.profile_revision = 'rev-2'
    service._refresh_profiles()
    assert service._full_network_payload is None and service._full_network_revision is None, 'a new profile revision drops it'
    rebuilt = service.full_network()
    assert rebuilt['source_revision'] == service.revision and service._full_network_payload is not None


def test_the_full_network_cache_survives_polls_for_a_reader_that_does_not_name_its_snapshot():
    from fec.surfaces.test_staffer_live_service import ProfilesFixture
    service, _ = memo_service(provider=ProfilesFixture(), settle=False)
    service.full_network()
    cached = service._full_network_payload
    for _ in range(3):
        service._refresh_profiles()
    assert service._full_network_payload is cached


def legacy_cached_full_network(service):
    """What full_network answered from its cache before each answer was stamped with its serve time."""
    return {**service._full_network_payload, **service._current_metadata(), **service._completion_state()}


def test_a_cached_full_network_is_stamped_with_the_time_it_is_served_not_the_time_it_was_built(monkeypatch):
    service, _ = memo_service()
    stamps = iter('2026-10-03T00:00:%02dZ' % number for number in range(1, 30))
    monkeypatch.setattr(service_module, 'utcnow', lambda: next(stamps))
    built = service.full_network()                      # built on first use, stamped when built
    assert built['generated_at'] == '2026-10-03T00:00:01Z' and service._full_network_payload is not None
    served = [service.full_network() for _ in range(3)]  # from the cache: the same content, a fresh stamp each time
    assert [answer['generated_at'] for answer in served] == ['2026-10-03T00:00:%02dZ' % number for number in (2, 3, 4)]
    for answer in served:
        assert answer['nodes'] == built['nodes'] and answer['edges'] == built['edges'] and answer['revision'] == built['revision']
    assert service._full_network_payload['generated_at'] == built['generated_at'], 'the cached payload itself is not rewritten'


def test_the_serve_time_wins_over_a_generated_at_the_poll_metadata_carries(monkeypatch):
    service, _ = memo_service()
    service.full_network()
    service.metadata = {**service.metadata, 'generated_at': 'poll-metadata-time'}
    monkeypatch.setattr(service_module, 'utcnow', lambda: 'serve-time')
    assert service.full_network()['generated_at'] == 'serve-time', 'as when the payload was rebuilt on every poll'


def test_negative_control_the_pre_fix_cached_answer_carries_the_build_time(monkeypatch):
    service, _ = memo_service()
    stamps = iter('2026-10-03T00:00:%02dZ' % number for number in range(1, 30))
    monkeypatch.setattr(service_module, 'utcnow', lambda: next(stamps))
    built = service.full_network()
    first, second = legacy_cached_full_network(service), legacy_cached_full_network(service)
    assert first['generated_at'] == second['generated_at'] == built['generated_at'], 'the harness must see the stale stamp'
