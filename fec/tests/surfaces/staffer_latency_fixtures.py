"""Shared fixtures for the staffer latency tests (not a test module)."""
import threading

from fec.surfaces.staffer_live_service import LiveService
from fec.surfaces.test_staffer_live_service import ProfilesFixture, fixture_graph


def profile_node(uuid, label, **extra):
    ident = 'politico:' + uuid
    return {'id': ident, 'kind': 'person', 'label': label, 'member_ids': [], 'committee_ids': [], 'policy_ids': [],
            'source_profile_uuid': uuid, 'current_positions': ['Counsel for Rep. Example'], **extra}


class MemoProfiles(ProfilesFixture):
    """A profile reader that, like IncrementalProfiles, names its accepted snapshot, and counts what is asked of it."""

    def __init__(self, labels=('Ada Lovelace', 'Alan Turing', 'Grace Hopper', 'Alma Adams Jr')):
        super().__init__()
        self.profile_revision = 'rev-1'
        self.profile_digests = {}
        self.accepted_prefix_sha256, self.accepted_receipt_digest = 'prefix-1', 'receipt-1'
        self.nodes = [profile_node(f'u{i}', label, last_reported_at='2026-0%d-01' % (i + 1)) for i, label in enumerate(labels)]
        self.accepted = [{'profile_uuid': f'u{i}'} for i in range(len(labels))]
        self.refreshes = self.graphs = 0

    def refresh(self):
        self.refreshes += 1
        self.metadata = {**self.metadata, 'cycle': self.refreshes}  # a new summary every cycle, as the real reader's is
        return self.metadata

    def graph(self, query, limit):
        self.graphs += 1
        return {'nodes': self.nodes, 'edges': []}


def office_graph():
    """The fixture graph plus one member office with two current staff, so the office typeahead has an answer."""
    graph = fixture_graph()
    graph.nodes['office:test'] = {'id': 'office:test', 'kind': 'container', 'container_kind': 'member_office',
                                  'label': '2021 HON. TEST OFFICE', 'member_ids': [], 'committee_ids': [], 'policy_ids': []}
    for person in ('person:gordon', 'person:stephanie'):
        graph.nodes[person]['stints'][0]['status'] = 'current'
    graph.reindex()
    return graph


def memo_service(settle=True, provider=None, graph=None):
    """A ready service over a memo-capable reader. The forced first refresh records what it built, so a poll with
    unchanged inputs reuses it; with settle (the default) one such poll has already run."""
    service = LiveService('fixture', graph or office_graph())
    service.profiles = provider or MemoProfiles()
    service._refresh_profiles(force=True)
    service.ready, service.stage = True, 'Ready'
    if settle:
        service._refresh_profiles()
    return service, service.profiles


class LockHeld:
    """Another thread holds service.lock until the block ends or `seconds` pass."""

    def __init__(self, service, seconds=3.0):
        self.service, self.seconds = service, seconds

    def __enter__(self):
        self.entered, self.release = threading.Event(), threading.Event()

        def run():
            with self.service.lock:
                self.entered.set()
                self.release.wait(self.seconds)
        self.thread = threading.Thread(target=run, daemon=True, name='lock-holder')
        self.thread.start()
        assert self.entered.wait(2), 'the holder never took the lock'
        return self

    def __exit__(self, *exc):
        self.release.set()
        self.thread.join(5)