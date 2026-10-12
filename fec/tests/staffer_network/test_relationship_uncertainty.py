import itertools
import json
import math
from datetime import date

import pytest

from fec.staffer_network.relationship_uncertainty import uncertainty
from fec.staffer_network.closeness_math import ExposureIndex
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.staffer_live_server import Handler
from fec.tests.surfaces.test_staffer_work_time_routes import episode, person

DAY = date(2026,9,10).toordinal()


def support(context='office', exposure=10, age=0):
    return {'employer_id': context, 'context_group': context,
            'effective_exposure_days': exposure, 'last_overlap_day': DAY-age}


def test_hand_calculation_and_missing_sentiment_remains_unknown():
    result = uncertainty([support(age=1826)], DAY)
    expected = .35 + .25 + math.log(2)*1826/(365.2425*5)
    assert result['cost'] == pytest.approx(expected)
    assert result['factor'] == pytest.approx(math.exp(-expected))
    assert result['relationship_sign'] == 'unknown'
    assert result['calibrated_probability'] is False


def test_context_concentration_and_weak_recent_context_does_not_erase_age():
    single = uncertainty([support(age=3650)], DAY)
    split_same = uncertainty([support(exposure=5,age=3650)]*2, DAY)
    assert split_same['cost'] == pytest.approx(single['cost'])
    different = uncertainty([support('x',5,3650),support('y',5,3650)],DAY)
    assert different['context_concentration'] == .5
    assert different['components']['unverified_usability'] == single['components']['unverified_usability']
    token = uncertainty([support('x',1000,3650),support('y',1,0)],DAY)
    assert token['weighted_years_since_overlap'] > 9.9


def fixture():
    nodes = {p:person(p.upper()) for p in 'abd'}
    rows = []
    for a,b,c,s,e in [('a','d','old','2000-01-01','2005-01-01'),
                       ('a','b','new1','2024-01-01','2026-09-10'),
                       ('b','d','new2','2024-01-01','2026-09-10')]:
        nodes[c]={'kind':'container','container_kind':'member_office','member_bioguide':c}
        rows.extend(episode(p,c,s,e) for p in (a,b))
    return AdvocacyRouter(nodes,rows,'test','2026-09-10')


def test_penalty_changes_search_and_matches_exhaustive_independent_costs():
    router=fixture()
    baseline=router.route('a','d',{'objective':'work_time','route_count':3,'alternative_strategy':'ranked','uncertainty_weight':0})
    adjusted=router.route('a','d',{'objective':'work_time','route_count':3,'alternative_strategy':'ranked'})
    assert baseline['node_ids']==['a','d']
    assert adjusted['node_ids']==['a','b','d']
    # Compute from structural outputs using the mathematical definition, not
    # the penalty implementation. The fixture has one context per pair.
    index=router.closeness_routes.index(router._options({'objective':'work_time'}))
    expected=[]
    for path in [('a','d'),('a','b','d')]:
        cost=0
        for a,b in zip(path,path[1:]):
            raw=index.pair(a,b)
            day=raw['workplaces'][0]['last_overlap_day']
            cost += -math.log(.8*raw['strength'])+.6+math.log(2)*(DAY-day)/(365.2425*5)
        expected.append(cost)
    assert [r['cost'] for r in adjusted['routes']]==pytest.approx(sorted(expected))
    for result in adjusted['routes']:
        assert result['connection_score']==pytest.approx(result['unpenalized_connection_score']*result['uncertainty_factor'])
    assert adjusted['routes'][1]['dependence_on_best_route']['shared_staff_edges']==0
    # Cache keys include the scenario weight.
    assert router.route('a','d',{'objective':'work_time','route_count':3,'alternative_strategy':'ranked','uncertainty_weight':0})['node_ids']==['a','d']
    json.dumps(adjusted,allow_nan=False)


def test_duplicate_payroll_and_profiles_never_reduce_uncertainty():
    rows=[episode('a','x','2020-01-01','2021-01-01'),episode('b','x','2020-01-01','2021-01-01')]
    original=ExposureIndex(rows)
    duplicate=ExposureIndex(rows*5,census_episodes=rows*5)
    assert original.pair('a','b')==duplicate.pair('a','b')
    assert uncertainty(original.pair('a','b')['workplaces'],DAY)==uncertainty(duplicate.pair('a','b')['workplaces'],DAY)


def test_office_anchor_penalty_includes_zero_edge_routes_and_is_not_double_applied():
    router=fixture()
    options={'objective':'work_time','_target_office':'new1'}
    result=router.route_many('a',['a','b'],options)
    assert result['node_ids']==['a']
    anchor=result['office_anchor_strength']
    assert result['uncertainty_cost']==pytest.approx(.6+math.log(2)/(365.2425*5))
    assert result['connection_score']==pytest.approx(anchor*result['uncertainty_factor'])
    assert result['uncertainty_cost']==result['office_anchor_uncertainty']['cost']


@pytest.mark.parametrize('weight',[-1,4,float('inf'),float('nan'),'bad'])
def test_invalid_scenario_weight(weight):
    with pytest.raises(ValueError): AdvocacyRouter._options({'uncertainty_weight':weight})


def test_http_accepts_scenario_weight():
    assert Handler.routing_options(None,{'uncertainty_weight':['.5']})['uncertainty_weight']==.5


def test_age_increases_penalty_without_relabeling_sign():
    rows=[support()]
    recent=uncertainty(rows,DAY);older=uncertainty(rows,DAY+365)
    assert older['factor']<recent['factor']
    assert older['relationship_sign']==recent['relationship_sign']=='unknown'
