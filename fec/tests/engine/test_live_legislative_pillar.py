import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from engine.live_legislative_pillar import measure,advancement,percentranks


def row(member='A',bill='H.R.1',sign=1,role='sponsor',status='Introduced'):
    weight={'sponsor':3,'original':2,'cosponsor':1}[role]
    return {'member':member,'bill':bill,'congress':119,'effective_category':'Charter Modernization',
            'effective_role':role,'disposition':'selected','contribution':sign*weight,
            'decay_weight':1,'sign':sign,'tracker_status':status,
            'direction_mechanism':'automated-official-evidence-policy'}


def run(rows,bills=None):
    return measure(['A','B','C'],rows,bills or {},{'Charter Modernization':1},
                   {'Charter Modernization':0.1},[0.7,0.3])


def test_new_bill_reaches_the_headline_pillar_without_workbook_cache():
    before=run([row('B')])
    after=run([row('B'),row('A')])
    assert after['agenda']['A']==3
    assert after['pillar']['A']>before['pillar']['A']
    assert after['automatic_bill_effects'][-1]['agenda_contribution']==3


def test_official_advancement_changes_efficiency_and_pillar():
    rows=[row('A'),row('B')]
    before=run(rows)
    after=run(rows,{'119:hr:1':{'latest_action':{'text':'On passage Passed by recorded vote.'}}})
    assert after['efficiency']['A']>before['efficiency']['A']
    assert after['pillar']['A']>before['pillar']['A']


def test_direction_is_applied_once_and_cosponsor_has_no_success_credit():
    result=run([row('A',sign=-1,status='Passed House'),row('B',role='cosponsor',status='Passed House')])
    assert result['agenda']['A']==-3
    assert result['efficiency']['A']<0
    assert result['efficiency']['B']==0


def test_withdrawn_and_superseded_rows_do_not_reach_rank():
    r=row();r['disposition']='official-membership-withdrawn-or-role-superseded'
    assert run([r])['agenda']['A']==0


def test_duplicate_selected_evidence_fails():
    with pytest.raises(ValueError,match='Duplicate selected'):run([row(),row()])


@pytest.mark.parametrize('text',['On motion to suspend the rules and pass the bill Failed.',
    'On motion to recommit Passed by recorded vote.','Amendment agreed to in House.'])
def test_procedural_or_failed_vote_does_not_invent_bill_advancement(text):
    assert advancement('',text) is None


def test_percentiles_preserve_ties_and_excel_truncation():
    assert percentranks({'A':0,'B':1,'C':1,'D':2})=={'A':0,'B':0.33333,'C':0.33333,'D':1}
