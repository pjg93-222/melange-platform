"""Negative controls for identity, missing evidence, and relationship semantics."""
import json
from itertools import product
from types import SimpleNamespace

import pytest

from fec.surfaces.staffer_connection_facts import DIMENSIONS
from fec.surfaces.staffer_identity_auto import compatible, resolve_identities
from fec.surfaces.staffer_overlap_routes import OverlapRoutes
from fec.surfaces.staffer_payroll_identity import load_payroll_identity
from fec.surfaces.staffer_routing import AdvocacyRouter


@pytest.mark.parametrize('left,right', [
    ('Robert de los Santos', 'Robert Santos'),
    ('Maria de las Nieves', 'Maria Nieves'),
    ('Mary Ann Smith-Jones', 'Mary Jones'),
    ('Jean Van Buren', 'Jean Buren'),
])
def test_source_proof_cannot_erase_a_compound_surname(left, right):
    proof = {'path': '/source', 'sha256': 'verified'}
    key = {'namespace': 'directory', 'value': 'duplicate', 'verified_unique': True, 'provenance': proof}
    nodes = {i: {'kind': 'person', 'label': label, 'external_ids': [key]} for i, label in [('a', left), ('b', right)]}
    assert not compatible(nodes['a'], nodes['b'])
    assert not resolve_identities(nodes, [], [], {}, [])['identity_aliases']


@pytest.mark.parametrize('left,right', [
    ('Greg J Mesack', 'Gregory James Mesack'),
    ('Robert de los Santos', 'Robert de los Santos'),
    ('Mary Ann Smith-Jones', 'Mary Ann Smith-Jones'),
    ("Joseph O'Neill", 'Joe O’Neill'),
    ('Kathleen Connery-Dawe', 'Kathleen Connery Dawe'),
    ('Eduardo Tapia Jr-Urbieta', 'Eduardo Tapia Jr.-Urbieta'),
])
def test_surname_guard_retains_supported_spelling_and_middle_initial_cases(left, right):
    assert compatible({'label': left}, {'label': right})


def test_ambiguous_formal_first_name_classes_are_not_new_positive_proof():
    assert not compatible({'label': 'Patrick Smith'}, {'label': 'Patricia Smith'})


@pytest.mark.parametrize('generation', ['20260907_v7', '20260908_v8', '20990101_v99'])
@pytest.mark.parametrize('missing', ['config', 'bundle'])
def test_installed_spine_never_uses_fixture_fallback(generation, missing, tmp_path):
    (tmp_path / 'audits/staffer_network' / f'hill_personnel_spine_{generation}_primary').mkdir(parents=True)
    config = tmp_path / 'config.json'
    if missing == 'bundle':
        config.write_text(json.dumps({'path': 'missing/bundle.gz', 'sha256': 'expected'}))
    with pytest.raises(ValueError, match='Payroll identity .*missing'):
        load_payroll_identity(tmp_path, config)


def test_empty_fixture_can_still_omit_payroll(tmp_path):
    assert load_payroll_identity(tmp_path, tmp_path / 'absent.json') is None


def router(episodes=(), features=None):
    features = features or {'a': {}, 'b': {}}
    nodes = {p: {'id': p, 'kind': 'person', 'label': 'Person ' + p} for p in features}
    facts = SimpleNamespace(features={p: {key: {v: {'label': v, 'sources': [{'fixture': True}]} for v in values.get(key, [])}
                                            for key, _, _ in DIMENSIONS} for p, values in features.items()})
    result = AdvocacyRouter(nodes, list(episodes), 'evidence-test', '2026-09-08')
    result.overlap_routes = OverlapRoutes(result, facts)
    return result


def episode(person, start='2020-01-01', end='2021-01-01', **extra):
    return {'person_id': person, 'employer_id': 'office', 'start': start, 'end_exclusive': end, **extra}


def test_uncertain_report_overlap_matches_independent_exhaustive_day_sets():
    # Enumerate every possible service day in a three-day payment window.
    # A claim is admitted only if every interpretation contains a common day.
    from datetime import date, timedelta
    epoch = date(2020, 1, 1)
    for a_uncertain, b_uncertain, offset in product((False, True), (False, True), range(-3, 4)):
        left_days, right_days = set(range(0, 3)), set(range(offset, offset+3))
        possibilities_a = [{d} for d in left_days] if a_uncertain else [left_days]
        possibilities_b = [{d} for d in right_days] if b_uncertain else [right_days]
        expected = all(a & b for a, b in product(possibilities_a, possibilities_b))
        episodes = [episode('a', str(epoch), str(epoch+timedelta(days=3)),
                            date_evidence='presence_within_report' if a_uncertain else 'service_dates'),
                    episode('b', str(epoch+timedelta(days=offset)), str(epoch+timedelta(days=offset+3)),
                            date_evidence='presence_within_report' if b_uncertain else 'service_dates')]
        result = router(episodes).route('a', 'b')
        assert (result['status'] == 'found') == expected
        if expected:
            hop = result['hops'][0]
            assert hop['overlap_duration_days'] == (None if a_uncertain or b_uncertain else len(left_days & right_days))
            assert not hop['personal_familiarity_established']


@pytest.mark.parametrize('objective', ['overlap_strength', 'overlap_fewest_steps', 'overlap_balanced'])
def test_shared_activity_cannot_earn_employment_points(objective):
    episodes = [episode(p, employer_id='activity:1', relationship='co_reported_lobbying_activity') for p in ('a', 'b')]
    r = router(episodes)
    assert r.route('a', 'b')['hops'][0]['relationship'] == 'co_reported_lobbying_activity'
    assert r.route('a', 'b', {'objective': objective})['status'] == 'unreachable'
    r = router(episodes, {'a': {'party': ['D']}, 'b': {'party': ['D']}})
    hop = r.route('a', 'b', {'objective': objective})['hops'][0]
    assert hop['strength'] == 4
    assert hop['work_evidence'] is None
    assert hop['evidence_class'] == 'shared_background_only'


def test_shared_latest_employer_does_not_establish_concurrent_work_or_familiarity():
    r = router(features={'a': {'current_employment': ['office']}, 'b': {'current_employment': ['office']}})
    hop = r.route('a', 'b', {'objective': 'overlap_balanced'})['hops'][0]
    assert hop['temporal_status'] == 'overlap_unconfirmed'
    assert not hop['temporal_overlap_established'] and not hop['personal_familiarity_established']
    assert hop['evidence_class'] == 'shared_background_only'
    assert hop['overlaps'][0]['label'] == 'Same employer in latest records'


def test_dated_work_survives_a_cheaper_activity_and_keeps_its_evidence():
    episodes = [episode(p) for p in ('a', 'b')]
    episodes += [episode(p, employer_id='activity:1', relationship='co_reported_lobbying_activity') for p in ('a', 'b')]
    hop = router(episodes).route('a', 'b', {'objective': 'overlap_balanced'})['hops'][0]
    assert hop['strength'] == 64
    assert hop['evidence_class'] == 'dated_work_overlap'
    assert hop['work_evidence']['relationship'] == 'overlapped_tenure'
    assert hop['temporal_overlap_established']


def test_excluded_user_assertions_are_not_shown_as_route_proof():
    r = router(features={'a': {'current_employment': ['office']}, 'b': {'current_employment': ['office']}})
    for p in ('a', 'b'):
        r.overlap_routes.facts.features[p]['current_employment']['office']['sources'].append({'user_asserted': True})
    hop = r.route('a', 'b', {'objective': 'overlap_balanced', 'include_user_asserted': False})['hops'][0]
    assert not hop['user_asserted']
    assert not any(s.get('user_asserted') for sources in hop['overlaps'][0]['matches'][0]['sources'].values() for s in sources)
    included = r.route('a', 'b', {'objective': 'overlap_balanced', 'include_user_asserted': True})['hops'][0]
    assert included['user_asserted']


def test_observed_report_core_is_not_relabelled_as_exact_service_duration():
    nodes = {p: {'id': p, 'kind': 'person', 'label': 'Person ' + p, 'stints': [{
        'container_id': 'office', 'core_start': '2020-01-01', 'core_end_exclusive': '2021-01-01',
        'date_precision': 'REPORT_PERIOD', 'container_kind': 'member_office'}]} for p in ('a', 'b')}
    graph = SimpleNamespace(nodes=nodes, load=lambda: None)
    r = AdvocacyRouter.from_static(graph, as_of='2026-09-08')
    direct = r.route('a', 'b')['hops'][0]
    assert direct['evidence_class'] == 'observed_report_core_overlap'
    assert direct['overlap_duration_days'] is None
    assert direct['model_interval_overlap_days'] == 366
    assert direct['modeled_overlap'] and not direct['temporal_overlap_established']
    r.overlap_routes = OverlapRoutes(r, router().overlap_routes.facts)
    affinity = r.route('a', 'b', {'objective': 'overlap_balanced'})['hops'][0]
    assert affinity['evidence_class'] == direct['evidence_class']
    assert affinity['modeled_overlap'] and not affinity['temporal_overlap_established']


@pytest.mark.parametrize('reverse', [False, True])
def test_uncertain_window_covered_by_report_core_still_has_only_modeled_support(reverse):
    episodes = [episode('a', date_evidence='presence_within_report'),
                episode('b', date_precision='REPORT_PERIOD')]
    r = router(episodes[::-1] if reverse else episodes)
    hop = r.route('b' if reverse else 'a', 'a' if reverse else 'b', {'objective': 'overlap_balanced'})['hops'][0]
    assert hop['modeled_overlap'] and not hop['temporal_overlap_established']
    assert hop['evidence_class'] == 'observed_report_core_overlap'
    assert hop['work_evidence']['overlap_duration_days'] is None
    assert hop['work_evidence']['model_interval_overlap_days'] is None


@pytest.mark.parametrize('version', ['AFFINITY_VERSION', 'WORK_ROUTING_VERSION', 'IDENTITY_VERSION', 'NAME_STANDARD_VERSION'])
def test_model_version_change_invalidates_live_revision_and_cached_routes(version, monkeypatch, tmp_path):
    from fec.surfaces import staffer_live_service as live
    from fec.surfaces.staffer_graph_snapshot import StaticGraph
    graph = StaticGraph(tmp_path)
    graph._loaded = True
    graph.nodes = {'person:hill:person:a': {'id': 'person:hill:person:a', 'kind': 'person', 'label': 'Known Person', 'stints': []}}
    graph.reindex()
    service = live.LiveService(tmp_path, graph)
    service._capture_profiles = lambda: {'summary': {'coverage': {}, 'errors': [], 'freshness': {}},
        'revision': 'fixed', 'graph': {'nodes': [], 'edges': []}, 'digests': {},
        'receipt_sha256': 'fixed', 'prefix_sha256': 'fixed'}
    monkeypatch.setattr(live, 'load_coworker_assertions', lambda: [])
    try:
        service._refresh_profiles(force=True)
        before = service.revision
        service.router._cache['obsolete'] = {'status': 'found'}
        monkeypatch.setattr(live, version, 'changed-model-version')
        service._refresh_profiles(force=True)
        assert service.revision != before
        assert service.router.revision == service.revision
        assert not service.router._cache
    finally:
        service.route_executor.shutdown(wait=False)
