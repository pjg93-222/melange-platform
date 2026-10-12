import importlib.util
from pathlib import Path

import numpy as np
import pytest

from fec.staffer_network.ranking_math import (
    attributed_support, co_tenure_weight, eligible_container, rank_outreach_candidates, shared_role,
)

ROOT = Path(__file__).resolve().parents[3]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


builder = module('audits.staffer_network.ranking_builder', 'audits/staffer_network/build_staffer_social_network.py')
verifier = module('ranking_verifier', 'audits/staffer_network/verify_staffer_social_network.py')


def test_three_day_detail_does_not_equal_three_thousand_days():
    attenuation, shares = attributed_support([0, 1, 3], [3000, 3000, 3], [False] * 3)
    diversity, _, mass = builder.office_diversity_simpson(
        [0, 0], [1, 2], [1., 1.], [0, 1, 3], [0, 0, 1], 3, 1833,
        container_shares=shares)
    assert mass[0, 1] == pytest.approx(.5 * 3 / 3003)
    assert .0009 < diversity[0] < .0011  # equal split would give .375
    assert np.allclose(attenuation, 1)
    expected, _ = verifier.independent_office_diversity(
        3, 1833, [0, 0], [1, 2], [1, 1], [(0,), (0, 1)], [(1,), tuple(shares[1:])])
    assert np.allclose(expected, diversity, atol=1e-14)
    assert np.allclose(np.asarray(mass.sum(axis=1)).ravel(), 1)


def test_shared_support_keeps_real_work_and_preserves_positive_edges():
    damping, shares = attributed_support([0, 1, 3], [365, 365, 365], [True, True, False])
    assert damping.tolist() == pytest.approx([.05, .525])
    assert shares.tolist() == pytest.approx([1., .05/1.05, 1/1.05])
    weight = co_tenure_weight([365, 365], [1, 1], [.75, .75], damping)
    baseline = co_tenure_weight(365, 1, .75)
    assert weight[0] == pytest.approx(.05 * baseline)
    assert weight[1] == pytest.approx(.525 * baseline)
    assert np.all(weight > 0)


@pytest.mark.parametrize('key', ['INTERN COMPENSATION - SMITH', '2025 INTERN COMPENSATION - SMITH'])
def test_intern_payroll_headers_are_not_workplaces(key):
    assert not eligible_container('member_office', key)
    assert eligible_container('member_office', 'HON:S000001')
    assert eligible_container('committee', 'INTERNATIONAL RELATIONS')
    assert not eligible_container('admin', 'HOUSE PAYMASTER')


def test_title_predicate_does_not_blacklist_unrelated_roles():
    assert shared_role(' shared  employee ')
    assert shared_role('SHARED EMPLOYEE (OTHER COMPENSATION)')
    assert not shared_role('SHARED FINANCIAL ADMINISTRATOR')
    assert not shared_role('CHIEF OF STAFF')


@pytest.mark.parametrize('ptr,days,shared', [([0, 0], [], []), ([0, 1], [0], [False]),
                                          ([0, 1], [float('nan')], [False]),
                                          ([0, 2], [3], [False])])
def test_invalid_support_fails_closed(ptr, days, shared):
    with pytest.raises(ValueError):
        attributed_support(ptr, days, shared)


def test_outreach_changes_only_tied_rank_order_and_exposes_remaining_ties():
    rows = [{'node_id': n, 'pagerank_085': p} for n, p in [('a', .1), ('b', .1), ('c', .2)]]
    first = rank_outreach_candidates(rows, {'a': 9, 'b': 1, 'c': 100}, {'a': 1, 'b': 1, 'c': 100})
    assert [r['node_id'] for r in first] == ['c', 'b', 'a']
    second = rank_outreach_candidates(rows, {'a': 1, 'b': 9}, {'a': 1, 'b': 1})
    assert [r['node_id'] for r in second] == ['c', 'a', 'b']
    unresolved = rank_outreach_candidates(rows[:2], {}, {})
    assert all(r['outreach_tie_size'] == 2 and r['outreach_route_cost'] is None for r in unresolved)


def test_actual_weight_path_is_bounded_monotonic_and_scalar_vector_equal():
    days = np.array([0, 1, 3, 365, 3000, 100000])
    vector = co_tenure_weight(days, 1, .75, .05)
    assert np.all(np.diff(vector) >= 0)
    assert np.all((vector >= 0) & (vector <= .0375 + 1e-15))
    assert np.array_equal(vector, np.array([co_tenure_weight(d, 1, .75, .05) for d in days]))


def test_bridge_ranking_uses_sustained_connections_not_office_count():
    from fec.surfaces.build_staffer_network_section import build_global
    # a has five trivial offices; b has two sustained workplaces; c has only a
    # payroll pool. Counting offices would rank a first and let c in the pool.
    people = [(p, 'S1', 10, .1, 0, .5, 0, 0, count, 0)
              for p, count in [('a', 5), ('b', 2), ('c', 10)]]
    scores = {'people': people, 'office_strengths': {
        'a': {'a1': .01, 'a2': .01, 'a3': .01, 'a4': .01, 'a5': .01},
        'b': {'b1': 10, 'b2': 10}, 'c': {'pool': 100}},
        'component_ids': {0}, 'largest_component': 3, 'person_rows': 3,
        'attached_any': 3, 'attached_roster': 3}
    trajectories = {'member_offices': {
        'a': {str(i): {'a' + str(i)} for i in range(1, 6)},
        'b': {'1': {'b1'}, '2': {'b2'}}, 'c': {}}}
    result = build_global({}, scores, {}, {'edge_counts': {
        'co_tenure_zero_overlap_REJECTED_never_emitted': 1}}, trajectories)
    assert [p['node_id'] for p in result['top_bridge_people']] == ['b', 'a']
    assert result['top_bridge_people'][0]['bridge_connection_strength'] == 10


def test_query_distances_use_the_full_weighted_person_graph():
    from fec.surfaces.staffer_outreach_ranking import route_costs
    # Direct weak contact (cost 10) loses to two strong ties (cost 2).
    costs = route_costs(4, [0, 0, 1], [2, 1, 2], [.1, 1., 1.], 0, 2)
    assert costs[0, 2] == 2
    assert costs[1, 0] == 2
    assert np.isinf(costs[:, 3]).all()


def test_verifier_rejects_laundered_or_nonfinite_attribution():
    from copy import deepcopy
    row = {'container_guaranteed_days': [365], 'container_shared_role': [True],
           'container_mass_shares': [1.], 'container_ids': ['container:office:house:HON:A000001'],
           'shared_role_factor': .05, 'shared_role_attenuation': .05,
           'kappa': 1., 'tier_strength': .75, 'guaranteed_days': 365,
           'w_tiered_lower': float(co_tenure_weight(365, 1, .75, .05))}
    assert verifier.attribution_valid(row, .05)
    for key, value in [('w_tiered_lower', row['w_tiered_lower'] * 20),
                       ('w_tiered_lower', float('nan')), ('container_mass_shares', [.5]),
                       ('container_shared_role', [False]), ('shared_role_factor', 1),
                       ('container_ids', ['container:office:senate:INTERN COMPENSATION - SMITH'])]:
        broken = deepcopy(row)
        broken[key] = value
        assert not verifier.attribution_valid(broken, .05), key
