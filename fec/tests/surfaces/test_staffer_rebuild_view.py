"""An identity rebuild swaps the replacement in with one view, and typeahead keeps answering from whole states while it runs."""
from datetime import datetime, timezone
import json
import threading
import time

import pytest

from fec.surfaces import staffer_identity_resolution as resolution
from fec.surfaces import staffer_live_service as live
from fec.surfaces.test_staffer_live_service import ProfilesFixture, fixture_graph


class References:
    seeds = []

    def __init__(self, path):
        self.path = path

    def references(self):
        return []

    def revision(self):
        return 'fixture-reference-revision'

    def enqueue_people(self, nodes, source_revision=None):
        pass

    def enqueue(self, groups):
        pass

    def status(self):
        return {'status': 'cycle_complete'}


@pytest.fixture
def service(tmp_path, monkeypatch):
    assertions = tmp_path / 'assertions.json'
    assertions.write_text(json.dumps({'keep_separate_pairs': [], 'coworker_assertions': []}))
    monkeypatch.setattr(resolution, 'USER_ASSERTIONS', assertions)
    graph = fixture_graph()
    graph.nodes['person:gordon'].update(lda_source_person_id='lda:lobbyist:1', source_summary='Old Council', last_reported_at='2020-01-01')
    graph.nodes['person:other-gordon'].update(lda_source_person_id='lda:lobbyist:2', source_summary='New Council', last_reported_at='2026-09-01',
                                              employment_history=[{'organization': 'New Council', 'provenance': {'sha256': 'source2'}}])
    svc = live.LiveService(tmp_path, graph)
    svc.profiles = ProfilesFixture()
    svc.profiles.path = tmp_path / 'profiles.jsonl'
    svc.identity_web = References(tmp_path / 'references.sqlite')
    svc.evidence = {'episodes': [], 'user_assertions': [{'person_ids': ['person:other-gordon', 'person:stephanie'],
                                                         'as_of': datetime.now(timezone.utc).date().isoformat()}]}
    svc._source_nodes, svc._source_edges, svc._source_episodes = dict(graph.nodes), list(graph.edges), []
    svc._refresh_profiles(force=True)
    svc.ready, svc.stage = True, 'Ready'
    monkeypatch.setattr(live, 'load_inventory', lambda *args: {'nodes': {}, 'identity_aliases': {}, 'identity_groups': [], 'sources': []})
    monkeypatch.setattr(live, 'load_identity_resolution', lambda *args: {'records': {}, 'candidate_groups': [], 'sources': [], 'counts': {}})
    return svc


def merged():
    group = {'primary_id': 'person:gordon', 'alias_ids': ['person:other-gordon'], 'source_person_ids': ['lda:lobbyist:1', 'lda:lobbyist:2'],
             'display_name': 'Gordon Holzberg', 'status': 'REFERENCE_RESOLVED_SAME_PERSON', 'asserted_at': '2026-09-04',
             'provenance': {'citations': [], 'method': 'fixture'}}
    return {'identity_groups': [group], 'identity_aliases': {'person:other-gordon': 'person:gordon'},
            'unresolved_candidates': [], 'counts': {}, 'evidence_version': 'fixture'}


def test_the_rebuild_publishes_the_replacements_view_and_forgets_what_belonged_to_the_old_one(service, monkeypatch):
    monkeypatch.setattr(live, 'resolve_identities', lambda *args: merged())
    old_view, old_revision = service._view, service.revision
    assert [n['id'] for n in service.search('person:other-gordon')['nodes']] == ['person:other-gordon']
    assert service.search_cache and service._refresh_memo is None
    service._rebuild_identity()
    view = service._view
    assert view is not old_view
    assert view.static is service.static and view.router is service.router and view.revision == service.revision
    assert view.profile_nodes is service.profile_nodes and view.aliases is service.identity_aliases
    assert view.revision != old_revision, 'the merge changed the source revision'
    assert [n['id'] for n in service.search('person:other-gordon')['nodes']] == ['person:gordon'], 'the alias resolves through the new view'
    assert service.search('Gordon Holzberg', 20)['revision'] == service.revision
    assert all(key[0] == service.revision for key in service.search_cache), 'no answer of the old state survives in the cache'


def test_typeahead_answers_from_whole_states_while_an_identity_rebuild_runs(service, monkeypatch):
    release, inside = threading.Event(), threading.Event()

    def slow_resolve(*args):
        inside.set()
        assert release.wait(10)
        return merged()
    monkeypatch.setattr(live, 'resolve_identities', slow_resolve)
    rebuild = threading.Thread(target=service._rebuild_identity, daemon=True)
    old_revision = service.revision
    rebuild.start()
    assert inside.wait(5)
    answers = []
    for _ in range(20):                      # the rebuild is mid-way: the old state still answers, promptly
        started = time.perf_counter()
        answers.append(service.search('Gordon Holzberg', 20))
        assert time.perf_counter() - started < 0.2
    assert {a['revision'] for a in answers} == {old_revision}
    assert {n['id'] for n in answers[0]['nodes']} == {'person:gordon', 'person:other-gordon'}
    release.set()
    rebuild.join(10)
    assert not rebuild.is_alive()
    after = service.search('Gordon Holzberg', 20)
    assert after['revision'] == service.revision != old_revision
    assert {n['id'] for n in after['nodes']} == {'person:gordon'}