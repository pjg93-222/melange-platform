from fec.staffer_network.reconcile_held_evidence import (
    covered_state, interval, norm, organization_key, quorum_decision, source_bounds,
)
from fec.staffer_network.reconcile_career_evidence import text_interval, contains_phrase
from fec.staffer_network.lda_composite_repair import bounded_page_url, CompositeRepairError
import pytest


def test_historical_query_does_not_change_legacy_inventory_contract():
    from fec.staffer_network.lda_composite_repair import LEGACY_YEARS
    assert LEGACY_YEARS == (2021,2022,2023,2024,2025,2026)
    assert 'filing_year=2020' in bounded_page_url(2020,'2020-01-01','2026-09-08',1)
    for value in (True, 1995, '2020', 9999):
        with pytest.raises(CompositeRepairError):bounded_page_url(value,None,None,1)


def test_career_text_does_not_choose_one_of_multiple_jobs_or_repair_bad_years():
    assert text_interval('Counsel 2010-2012', '2026-09-08')['start']=='2010-12-31'
    assert text_interval('Counsel 03/2010 - 07/2012', '2026-09-08')['start']=='2010-03-31'
    assert text_interval('Counsel 2019-2013', '2026-09-08')['status']=='REVERSED_DATE_BOUNDS'
    assert text_interval('Counsel 2010-2012 and adviser 2013-2014', '2026-09-08')['status']=='MULTIPLE_INTERVALS_REVIEW'
    assert not contains_phrase('representative smithson counsel','smith')


def test_coarse_dates_keep_guaranteed_interior_and_do_not_fill_missing_end():
    d = interval('2020-00-00', '2022-00-00', '2026-09-08')
    assert d['start'] == '2020-12-31'
    assert d['end_exclusive'] == '2022-01-02'
    assert interval('2020-01-01', None, '2026-09-08')['status'] == 'MISSING_DATE_BOUND'
    assert interval('2020-00-00', '2020-00-00', '2026-09-08')['status'] == 'POSSIBLE_OVERLAP_ONLY'


def test_bad_reversed_and_future_dates_withheld():
    assert source_bounds('2020-00-15') is None
    assert source_bounds('2021-02-29') is None
    assert interval('2023-01-01', '2022-01-01', '2026-09-08')['status'] == 'REVERSED_DATE_BOUNDS'
    assert interval('2026-10-01', '2026-12-01', '2026-09-08')['status'] == 'FUTURE_SOURCE_DATE'


def test_placeholders_are_not_career_claims():
    for text in (';', '...', ' '): assert covered_state(text) == 'EMPTY_OR_PUNCTUATION'
    for text in ('N/A', 'None', 'No covered positions'): assert covered_state(text) == 'EXPLICIT_NO_POSITION'
    assert covered_state('Chief of Staff, Rep. Smith') == 'SUBSTANTIVE_CLAIM_REQUIRES_RECONCILIATION'


def fixtures():
    q = {'id':'q1','label':'Jane A Smith','title':'Legislative Assistant','office':'Aaron Bean'}
    p = {'id':'p1','label':'Jane A Smith','payload':{'observed_at':'2026-09-01',
        'current_header_positions':['Legislative Assistant for Rep. Aaron Bean'],
        'employment_history':[{'title':'Legislative Assistant','employer':'Rep. Aaron Bean','date_text':'Jan 2024 - Present'}]}}
    return q,p


def test_identity_acceptance_requires_dated_history_and_unique_reciprocal_match():
    q,p = fixtures()
    decide = lambda: quorum_decision(q,[p],{'p1':{'q1'}},{norm(p['label']):{'p1'}})
    assert decide()[0] == 'ACCEPTED_SOURCE_BINDING'
    assert quorum_decision(q,[p],{'p1':{'q1','q2'}},{norm(p['label']):{'p1'}})[0] == 'IDENTITY_REVIEW_REQUIRED'
    p['payload']['employment_history'][0]['date_text'] = ''
    assert decide()[0] == 'IDENTITY_REVIEW_REQUIRED'


def test_identity_does_not_accept_name_conflicts_or_interns():
    q,p = fixtures()
    p['label'] = 'Jane B Smith'
    assert quorum_decision(q,[p],{'p1':{'q1'}},{norm(p['label']):{'p1'}})[0] == 'IDENTITY_REVIEW_REQUIRED'
    q['title'] = 'Intern'
    assert quorum_decision(q,[p],{'p1':{'q1'}},{norm(p['label']):{'p1'}})[0] == 'EXCLUDED_OR_UNKNOWN_ROLE'


def test_client_identity_requires_independent_attributes_and_retains_subsidiaries():
    row={'name_variants':['EXAMPLE NATIONAL TRADE ASSOCIATION'], 'description_variants':['National trade association for example manufacturers'],
         'state_variants':['DC'],'country_variants':['US']}
    key=organization_key(row)
    assert key
    assert organization_key({**row,'description_variants':[]}) is None
    assert organization_key({**row,'state_variants':[None]}) is None
    assert organization_key({**row,'state_variants':['DC','VA']}) is None
    assert organization_key({**row,'name_variants':['EXAMPLE NATIONAL TRADE ASSOCIATION FOUNDATION']}) != key
    assert organization_key({**row,'name_variants':['EXAMPLE NATIONAL TRADE ASSOCIATION DBA OTHER']}) is None
