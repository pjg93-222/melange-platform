"""Automatic identity rebuilds with source-free fixtures and temporary receipts."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from fec.surfaces import staffer_live_service as live
from fec.surfaces import staffer_identity_resolution as resolution
from fec.surfaces.test_staffer_live_service import fixture_graph, ProfilesFixture


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
    assertions.write_text(json.dumps({'keep_separate_pairs': [], 'coworker_assertions': [{'id': 'fixture', 'status': 'active', 'source_person_ids': ['person:other-gordon', 'person:stephanie'], 'as_of': live.local_date()}]}))
    monkeypatch.setattr(resolution, 'USER_ASSERTIONS', assertions)
    graph = fixture_graph()
    graph.nodes['person:gordon'].update(lda_source_person_id='lda:lobbyist:1',
        source_summary='Old Council', last_reported_at='2020-01-01')
    graph.nodes['person:other-gordon'].update(lda_source_person_id='lda:lobbyist:2',
        source_summary='New Council', last_reported_at='2026-09-01',
        employment_history=[{'organization': 'New Council', 'provenance': {'sha256': 'source2'}}])
    svc = live.LiveService(tmp_path, graph)
    svc.profiles = ProfilesFixture()
    svc.profiles.path = tmp_path / 'profiles.jsonl'
    svc.identity_web = References(tmp_path / 'references.sqlite')
    svc.evidence = {'episodes': [], 'user_assertions': [{'person_ids': [
        'person:other-gordon', 'person:stephanie'],
        'as_of': datetime.now(timezone.utc).date().isoformat()}]}
    svc._source_nodes = dict(graph.nodes)
    svc._source_edges = list(graph.edges)
    svc._source_episodes = []
    svc._refresh_profiles(force=True)
    svc.ready = True
    svc.stage = 'Ready'
    monkeypatch.setattr(live, 'load_inventory', lambda *args: {
        'nodes': {}, 'identity_aliases': {}, 'identity_groups': [], 'sources': []})
    monkeypatch.setattr(live, 'load_identity_resolution', lambda *args: {
        'records': {}, 'candidate_groups': [], 'sources': [], 'counts': {}})
    return svc


def result(merge=True):
    group = {'primary_id': 'person:gordon', 'alias_ids': ['person:other-gordon'],
        'source_person_ids': ['lda:lobbyist:1', 'lda:lobbyist:2'],
        'display_name': 'Gordon Holzberg', 'status': 'REFERENCE_RESOLVED_SAME_PERSON',
        'asserted_at': '2026-09-04', 'provenance': {'citations': [], 'method': 'fixture'}}
    return {'identity_groups': [group] if merge else [],
        'identity_aliases': {'person:other-gordon': 'person:gordon'} if merge else {},
        'unresolved_candidates': [], 'counts': {}, 'evidence_version': 'fixture'}


def test_automatic_merge_retains_native_history_latest_summary_and_current_alias(service, monkeypatch):
    monkeypatch.setattr(live, 'resolve_identities', lambda *args: result())
    source_edges = deepcopy(service._source_edges)
    service._rebuild_identity()
    node = service.static.nodes['person:gordon']
    assert node['source_person_ids'] == ['lda:lobbyist:1', 'lda:lobbyist:2']
    assert {n['id'] for n in node['source_identity_records']} == {'person:gordon', 'person:other-gordon'}
    assert node['source_summary'] == 'New Council'
    assert any(h['organization'] == 'New Council' for h in node['employment_history'])
    assert node['stints']
    assert service._resolve_id('person:other-gordon') == 'person:gordon'
    assert {e['person_id'] for e in service._user_episodes()} == {'person:gordon', 'person:stephanie'}
    assert service._source_edges == source_edges
    assert not node['canonical_identity_promoted']


def test_next_rebuild_restores_full_source_identities_before_resolving(service, monkeypatch):
    seen = []
    def resolve(nodes, *args):
        seen.append(set(nodes))
        return result(merge=len(seen) == 1)
    monkeypatch.setattr(live, 'resolve_identities', resolve)
    service._rebuild_identity()
    assert 'person:other-gordon' not in service.static.nodes
    service._rebuild_identity()
    assert all({'person:gordon', 'person:other-gordon'} <= ids for ids in seen)
    assert 'person:other-gordon' in service.static.nodes
    assert service._resolve_id('person:other-gordon') == 'person:other-gordon'


def test_successful_rebuild_does_not_mutate_held_old_graph_or_indexes(service, monkeypatch):
    old = service.static
    before = deepcopy(old.nodes)
    old_search = old.search_ids('Gordon')
    monkeypatch.setattr(live, 'resolve_identities', lambda *args: result())
    service._rebuild_identity()
    assert service.static is not old
    assert old.nodes == before
    assert old.search_ids('Gordon') == old_search
    assert 'person:other-gordon' in old.nodes


def test_failed_rebuild_keeps_last_good_graph_router_and_cached_response(service, monkeypatch):
    old_graph, old_router = service.static, service.router
    before = deepcopy(old_graph.nodes)
    encoded = service.encoded_snapshot('Gordon', 20)
    def fail(*args):
        raise ValueError('Reference fixture failed')
    monkeypatch.setattr(live, 'resolve_identities', fail)
    with pytest.raises(ValueError, match='Reference fixture failed'):
        service._rebuild_identity()
    assert service.ready and service.stage == 'Ready'
    assert service.static is old_graph and service.router is old_router
    assert old_graph.nodes == before
    assert service.encoded_snapshot('Gordon', 20) is encoded
    assert service.snapshot('Gordon', 20)['ready']


def test_overlay_normalizes_native_id_found_only_in_record_id_and_provenance():
    from fec.surfaces.staffer_person_inventory import apply_identity_overlay
    nodes = {
        'person:lda:lobbyist:100': {'id': 'person:lda:lobbyist:100', 'kind': 'person',
            'label': 'Greg Mesack', 'provenance': {'lda_source_person_id': 'lda:lobbyist:100'}},
        'person:lda:lobbyist:200': {'id': 'person:lda:lobbyist:200', 'kind': 'person',
            'label': 'Gregory Mesack', 'provenance': {'lda_source_person_id': 'lda:lobbyist:200'}},
    }
    group = {'primary_id': 'person:lda:lobbyist:100', 'alias_ids': ['person:lda:lobbyist:200'],
        'source_person_ids': list(nodes), 'display_name': 'Greg Mesack',
        'status': 'REFERENCE_RESOLVED_SAME_PERSON', 'asserted_at': '2026-09-04', 'provenance': {}}
    overlay = {'nodes': {}, 'sources': []}
    apply_identity_overlay(overlay, nodes, [group])
    merged = overlay['nodes']['person:lda:lobbyist:100']
    assert merged['source_person_ids'] == ['lda:lobbyist:100', 'lda:lobbyist:200']
    assert {r['id'] for r in merged['source_identity_records']} == set(nodes)


def profile(ident='politico:newly-verified-uuid', label='Verified New Person'):
    return {'id': ident, 'kind': 'person', 'label': label,
        'source_profile_uuid': ident.removeprefix('politico:'),
        'member_ids': [], 'committee_ids': [], 'policy_ids': [],
        'employment_history': [{'organization': 'Verified Council', 'title': 'Policy Director'}],
        'provenance': {'record': 'accepted-profile-row'}}


def test_previously_unseen_verified_profile_enters_resolver_with_bound_history(service, monkeypatch):
    new = profile()
    service.profiles.nodes = [new]
    service.profiles.accepted = [{'source_profile_uuid': new['source_profile_uuid']}]
    service.profiles.accepted_prefix_sha256 = 'verified-new-prefix'
    seen = {}
    def resolve(nodes, *args):
        seen.update(deepcopy(nodes))
        return result(False)
    monkeypatch.setattr(live, 'resolve_identities', resolve)
    service._rebuild_identity()
    assert new['id'] in seen
    assert seen[new['id']]['label'] == new['label']
    history = seen[new['id']]['employment_history']
    assert any(h['organization'] == 'Verified Council' and
               h['provenance']['sha256'] == live.digest(new) and
               h['provenance']['accepted_prefix_sha256'] == 'verified-new-prefix' and
               h['provenance']['receipt_sha256'] == 'receipt-one' for h in history)
    assert service.person(new['id'])['node']['label'] == new['label']


def test_verified_profile_name_replaces_placeholder_without_changing_uuid(service, monkeypatch):
    ident = 'politico:directory-uuid'
    placeholder = profile(ident, 'Name unavailable in source')
    placeholder.update(profile_pending=True, name_status='UNRESOLVED_SOURCE_NAME', employment_history=[])
    service._source_nodes[ident] = placeholder
    service.profiles.nodes = [profile(ident, 'Verified Proper Name')]
    service.profiles.accepted = [{'source_profile_uuid': 'directory-uuid'}]
    seen = {}
    def resolve(nodes, *args):
        seen.update(deepcopy(nodes))
        return result(False)
    monkeypatch.setattr(live, 'resolve_identities', resolve)
    service._rebuild_identity()
    assert seen[ident]['label'] == 'Verified Proper Name'
    assert seen[ident]['source_profile_uuid'] == 'directory-uuid'
    assert seen[ident]['employment_history']
    assert service.person(ident)['node']['label'] == 'Verified Proper Name'


def test_rejected_profile_metadata_does_not_replace_last_accepted_evidence(service, monkeypatch):
    accepted = profile()
    service.profiles.nodes = [accepted]
    service.profiles.accepted = [{'source_profile_uuid': accepted['source_profile_uuid']}]
    monkeypatch.setattr(live, 'resolve_identities', lambda *args: result(False))
    service._rebuild_identity()
    before = service.person(accepted['id'])['node']
    router = service.router
    revision = service._profile_revision
    service.profiles.metadata = {'coverage': {'verified': 1},
        'errors': ['New profile receipt rejected: digest mismatch'], 'freshness': {'stale': True}}
    service._refresh_profiles()
    assert service.ready and service.router is router
    assert service._profile_revision == revision
    assert service.person(accepted['id'])['node'] == before


def test_source_review_assertions_survive_profile_refresh_and_identity_rebuild(service, monkeypatch):
    accepted = profile()
    ident = accepted['id']
    assertion = {'assertion_id': 'sealed:history:1', 'layer': 'politico_employment_history',
                 'source_profile_uuid': accepted['source_profile_uuid'], 'operator_included': False,
                 'canonical_eligible': False, 'artifact_path': 'sealed-review-layer'}
    service.static.pending_source_assertions = {ident: [assertion]}
    service.static.receipt = {'files': [{'role': 'staffer_social_politico_review_layers', 'records': 1}]}
    service.profiles.nodes = [accepted]
    service.profiles.accepted = [{'source_profile_uuid': accepted['source_profile_uuid']}]
    monkeypatch.setattr(live, 'resolve_identities', lambda *args: result(False))
    for action in (lambda: service._refresh_profiles(force=True), service._refresh_profiles,
                   service._rebuild_identity, service._refresh_profiles):
        action()
        assert service.person(ident)['node']['review_assertions'] == [assertion]
        assert service.metrics['review_assertion_coverage']['served_unique'] == 1
    assert 'review_assertions' not in accepted


def test_incomplete_review_assertion_projection_cannot_report_ready(service):
    service.static.receipt = {'files': [{'role': 'staffer_social_politico_review_layers', 'records': 2}]}
    with pytest.raises(ValueError, match='Review assertion coverage mismatch'):
        service._refresh_profiles(force=True)


def test_profile_arriving_during_resolve_remains_pending_after_atomic_swap(service, monkeypatch):
    first, later = profile(), profile('politico:arrived-during-resolve', 'Later Verified Person')
    service.profiles.nodes = [first]
    service.profiles.accepted = [{'source_profile_uuid': first['source_profile_uuid']}]
    service.profiles.accepted_prefix_sha256 = 'first-prefix'
    consumed = service._capture_profiles()
    def resolve(nodes, *args):
        assert first['id'] in nodes and later['id'] not in nodes
        service.profiles.nodes = [first, later]
        service.profiles.accepted = [{'source_profile_uuid': n['source_profile_uuid']} for n in (first, later)]
        service.profiles.accepted_prefix_sha256 = 'later-prefix'
        return result(False)
    monkeypatch.setattr(live, 'resolve_identities', resolve)
    service._rebuild_identity()
    assert service._identity_profile_revision == consumed['revision']
    assert service._identity_profile_digests == consumed['digests']
    assert service._profile_revision != service._identity_profile_revision
    assert service.person(later['id'])['node']['label'] == later['label']
    assert service._identity_wakeup.is_set()
    second_seen = {}
    def resolve_second(nodes, *args):
        second_seen.update(nodes)
        return result(False)
    monkeypatch.setattr(live, 'resolve_identities', resolve_second)
    rebuild = service._rebuild_identity
    def rebuild_and_stop():
        rebuild()
        service._stop.set()
    monkeypatch.setattr(service, '_rebuild_identity', rebuild_and_stop)
    service.run_identity_apply_loop()
    assert later['id'] in second_seen
    assert service._identity_profile_revision == service._profile_revision
    assert later['id'] in service._identity_profile_digests


def test_profile_capture_proof_and_graph_are_one_immutable_observation(service):
    first = profile()
    service.profiles.nodes = [first]
    service.profiles.accepted = [{'source_profile_uuid': first['source_profile_uuid']}]
    service.profiles.accepted_prefix_sha256 = 'first-prefix'
    captured = service._capture_profiles()
    service.profiles.nodes = [profile('politico:later', 'Later Person')]
    service.profiles.accepted_prefix_sha256 = 'later-prefix'
    service.profiles.accepted_receipt_digest = 'later-receipt'
    assert captured['prefix_sha256'] == 'first-prefix'
    assert captured['receipt_sha256'] == 'receipt-one'
    assert captured['graph']['nodes'][0]['id'] == first['id']


def test_verified_profile_history_survives_automatic_canonical_overlay(service, monkeypatch):
    service.profiles.nodes = [profile('person:other-gordon', 'Gordon Holzberg')]
    service.profiles.accepted = [{'source_profile_uuid': 'other-gordon-profile'}]
    service.profiles.accepted_prefix_sha256 = 'new-profile-proof'
    expected_content_digest = live.digest(service.profiles.nodes[0])
    monkeypatch.setattr(live, 'resolve_identities', lambda *args: result())
    service._rebuild_identity()
    node = service.person('person:other-gordon')['node']
    assert node['id'] == 'person:gordon'
    assert any(h['organization'] == 'Verified Council' and
               h['provenance']['sha256'] == expected_content_digest and
               h['provenance']['accepted_prefix_sha256'] == 'new-profile-proof' and
               h['provenance']['receipt_sha256'] == 'receipt-one'
               for h in node['employment_history'])
    assert set(node['source_person_ids']) == {'lda:lobbyist:1', 'lda:lobbyist:2'}


def test_verified_profile_enrichment_retains_existing_source_affiliation_ids():
    prior = {'id': 'person:canonical', 'label': 'Verified Person',
        'member_ids': ['A000370'], 'committee_ids': ['HSBA'], 'policy_ids': ['BANKING'],
        'stints': [{'container_id': 'office:A000370', 'core_start': '2020-01-01'}]}
    incoming = profile('person:canonical', 'Verified Person')
    incoming['committee_ids'] = ['SSBK']
    merged = live.LiveService._merge_profile_node(prior, incoming, prior['id'])
    assert merged['member_ids'] == ['A000370']
    assert set(merged['committee_ids']) == {'HSBA', 'SSBK'}
    assert merged['policy_ids'] == ['BANKING']
    assert merged['stints'] == prior['stints']


def test_receipt_only_refresh_republishes_person_proof_without_rebuilding_routes(service, monkeypatch):
    accepted = profile()
    # The reader supplies a content hash independent of receipt/line-byte proof.
    accepted['profile_content_sha256'] = 'stable-profile-content-sha'
    accepted['provenance']['raw_line_sha256'] = 'first-line-bytes'
    service.profiles.nodes = [accepted]
    service.profiles.accepted = [{'source_profile_uuid': accepted['source_profile_uuid']}]
    monkeypatch.setattr(live, 'resolve_identities', lambda *args: result(False))
    service._rebuild_identity()
    previous = service.person(accepted['id'])['node']
    old_proof = deepcopy(previous['provenance'])
    router, revision = service.router, service.revision
    accepted_again = deepcopy(accepted)
    accepted_again['provenance']['raw_line_sha256'] = 'replacement-line-bytes'
    service.profiles.nodes = [accepted_again]
    service.profiles.accepted_prefix_sha256 = 'replacement-accepted-prefix'
    service.profiles.accepted_receipt_digest = 'replacement-receipt'
    service._refresh_profiles()
    current = service.person(accepted['id'])['node']
    assert service.router is router
    assert service.revision == revision
    assert current['provenance']['sha256'] == 'stable-profile-content-sha'
    assert current['provenance']['raw_line_sha256'] == 'replacement-line-bytes'
    assert current['provenance']['accepted_prefix_sha256'] == 'replacement-accepted-prefix'
    assert current['provenance']['receipt_sha256'] == 'replacement-receipt'
    assert previous['provenance'] == old_proof
    assert current['employment_history'][0]['provenance'] == current['provenance']
