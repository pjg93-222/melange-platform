from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from lobbying import industry_tiers as IT
from engine import primacy_factors as PF

def test_cu_board_uses_full_authority_instead_of_stale_desk_or_generalized_scores(monkeypatch):
    monkeypatch.setattr(IT.tier_util,'legislative_press_tiers',lambda names,root,mode=None:({'A':2,'B':1},{'A':14,'B':1}))
    rows=[{'name':'A','bioguide':'A1','chamber':'House','tier':1,'tierrank':1},
          {'name':'B','bioguide':'B1','chamber':'Senate','tier':2,'tierrank':14}]
    factors={'members':{'A':{'factor_ranked':.5,'mix':{'cu_primary':1}},
                        'B':{'factor_ranked':.8,'mix':{'cu_primary':2}}},
             '_constants':{'roster_baseline':.6}}
    result=IT._canonical_cu_board(rows,['A','B'],factors,{})
    assert [(r['member'],r['tier'],r['tierrank'],r['score']) for r in result['rows']]==[
        ('B',1,1,.8),('A',2,14,.5)]
    assert result['receipts']['authority']==IT.FACTOR_PATH

@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,0])
def test_adjudicated_weight_must_be_finite_positive(tmp_path,value):
    import json
    path=tmp_path/'credits.ndjson'
    path.write_text(json.dumps({'event_id':'E','member':'A','role_weight':value,'beneficiary_class':'cu_primary'})+'\n')
    with pytest.raises(ValueError,match='positive role_weight'):PF.load_adjudicated_credits(path)

def test_duplicate_acts_cannot_double_count():
    bill={'bill':'H.R.1','congress':'119','role':'sponsor'}
    with pytest.raises(ValueError,match='duplicate primacy act'):
        PF.compute({'members':[{'name':'A','bills':[bill,bill]}]}, {'H.R.1|119':{'class':'cu_primary'}})
