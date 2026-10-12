import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from stance.adjudicated_corroboration import from_routing
import moneyball_core as MC

def row(**changes):
    return dict({'member':'A','bill':'H.R.1','congress':119,
        'effective_category':'Payments','effective_role':'sponsor','sign':1,
        'contribution':3.0,'tracker_status':'Introduced','disposition':'selected'},**changes)

@pytest.mark.parametrize('changes',[
    {'disposition':'held'}, {'disposition':'excluded'}, {'disposition':'duplicate-lower-role'},
    {'sign':-1,'contribution':-3.0}, {'sign':0,'contribution':0.0},
    {'effective_role':'cosponsor','contribution':1.0},
])
def test_nonqualifying_evidence_cannot_corroborate(changes):
    assert from_routing([row(**changes)],['A'])=={}

def test_original_author_and_fresh_advancement_qualify():
    actual=from_routing([row(effective_role='original-cosponsor',contribution=2)],['A'],
                       {'119:hr:1':{'latest_action':{'text':'Passed House'}}})
    assert actual['A']['sponsored']=={'Payments'}
    assert actual['A']['advanced']=={'Payments'}
    assert actual['A']['direct_sponsored_bills']==set()

def test_letter_never_gets_a_passage_badge():
    result=from_routing([row(bill='LTR-TEST',tracker_status='Became Law')],['A'])
    assert result['A']['advanced']==set()

def test_duplicate_accepted_evidence_fails():
    with pytest.raises(ValueError,match='Duplicate'):
        from_routing([row(),row()],['A'])

def test_empty_accepted_evidence_does_not_disable_gate(monkeypatch):
    monkeypatch.setattr(MC,'bill_corroboration',lambda:{})
    monkeypatch.setattr(MC,'vote_corroboration',lambda:{})
    assert MC.cap_uncorroborated('A',{'Payments':'Champion'})[0]=={'Payments':'Ally'}

def test_live_policy_cannot_fall_back_to_unadjudicated_workbook(tmp_path,monkeypatch):
    import json
    path=tmp_path/'data/engine/sentinel_admission_policy.json';path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'live_legislative_pillar':True}))
    monkeypatch.setattr(MC,'p',lambda name:str(tmp_path/name))
    monkeypatch.setattr(MC,'_CORR',None)
    with pytest.raises(ValueError,match='requires adjudicated routing'):MC.bill_corroboration()
