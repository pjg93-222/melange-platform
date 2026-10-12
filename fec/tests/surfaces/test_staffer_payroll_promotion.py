import pytest

from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.tests.surfaces.test_staffer_work_time_routes import person, episode


def payroll(p, office='office:one', title='LEGISLATIVE DIRECTOR', precision='DAY'):
    return {'person_id':p,'employer_id':office,'start':'2020-01-01','end_exclusive':'2021-01-01',
        'date_precision':precision,'review_only':True,
        'provenance':{'source':'verified_static_trajectory','stint':{
            'title_last':title,'container_kind':'member_office','chamber':'house'}}}


def make(rows):
    router=AdvocacyRouter({p:person(p.upper()) for p in 'abc'},rows,'fixture','2026-09-10')
    router.operational_staffer_ids={'b','c'}
    return router


def test_payroll_fills_endpoint_gap_without_relabeling_as_directory():
    r=make([payroll('a'),episode('b','office:one','2020-01-01','2021-01-01')])
    before=r.route('a','b',{'objective':'work_time','work_sources':'directory'})
    assert before['status']=='insufficient_work_coverage'
    after=r.route('a','b',{'objective':'work_time'})
    assert after['status']=='found'
    assert after['work_source_coverage']['people']==2
    assert after['hops'][0]['work_sources']==['directory','payroll_trajectory']
    source=after['hops'][0]['provenance']['source_evidence']['a'][0]
    assert source['provenance']['source']=='verified_static_trajectory'
    assert source['role_scope']=='stint_summary_not_daily_assignment'
    assert r.route('a','b',{'objective':'work_time','work_sources':'directory'})['status']==before['status']


def test_promoted_shared_roles_are_diluted_and_admins_remain_outside_personal_work():
    normal=make([payroll('a'),payroll('b')]).route('a','b',{'objective':'work_time'})
    shared=make([payroll('a',title='SHARED EMPLOYEE'),payroll('b')]).route('a','b',{'objective':'work_time'})
    assert shared['hops'][0]['effective_exposure_days']==pytest.approx(.02*normal['hops'][0]['effective_exposure_days'])
    assert shared['cost']>normal['cost']
    rows=[payroll(p) for p in 'ab']
    for row in rows:row['provenance']['stint']['container_kind']='admin'
    assert make(rows).route('a','b',{'objective':'work_time'})['status']=='insufficient_work_coverage'


def test_report_windows_are_exposed_as_modeled_not_observed_daily_overlap():
    result=make([payroll('a',precision='REPORT_PERIOD'),payroll('b')]).route('a','b',{'objective':'work_time'})
    hop=result['hops'][0]
    assert hop['modeled_overlap'] and hop['modeled_exposure']
    assert not hop['temporal_overlap_established']
    assert hop['evidence_class']=='modeled_payroll_exposure'
    assert hop['overlap_duration_days'] is None
    assert hop['model_interval_overlap_days']==366


def test_duplicate_sources_do_not_double_count_and_detailed_roles_override_summaries():
    rows=[payroll('a'),payroll('b')]
    native={**payroll('a'),'title':'SHARED EMPLOYEE',
            'provenance':{'method':'payroll-identity-rows-v1','source_row_ids':['r1']}}
    r=make([*rows,native,native])
    result=r.route('a','b',{'objective':'work_time'})
    reference=make([payroll('a',title='SHARED EMPLOYEE'),payroll('b')]).route('a','b',{'objective':'work_time'})
    assert result['cost']==pytest.approx(reference['cost'])
    assert result['hops'][0]['work_sources']==['payroll_row','payroll_trajectory']


def test_trajectory_fills_only_uncovered_intervals_and_filters_remain_active():
    detailed=episode('a','office:one','2020-01-01','2020-07-01')
    r=make([payroll('a'),payroll('b'),detailed])
    result=r.route('a','b',{'objective':'work_time'})
    source=result['hops'][0]['provenance']['source_evidence']['a']
    assert sorted((s['start'],s['end_exclusive']) for s in source)==[('2020-01-01','2020-07-01'),('2020-07-01','2021-01-01')]
    assert r.route('a','b',{'objective':'work_time','include_review':False})['status']=='insufficient_work_coverage'
    with pytest.raises(ValueError):r.route('a','b',{'work_sources':'invented'})
