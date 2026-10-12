import copy
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from legislation.sentinel_admissions import validate_membership_projection

def document():
    return {'bills':{'119:hr:1':{'display':'H.R.1','congress':119,'areas':['Payments'],
        'sponsor':{'bioguide':'A','withdrawn_date':''},'original_cosponsors':[],
        'cosponsors':[{'bioguide':'B','withdrawn_date':'2026-09-01'}]}},
        'memberships':[{'repository_key':'119:hr:1','bill':'H.R.1','congress':'119','bioguide':'A',
                        'role':'sponsor','areas':['Payments'],'withdrawn':False,'withdrawn_date':''}],
        'unresolved_members':[{'key':'119:hr:1','bioguide':'B','role':'cosponsor'}]}

def test_official_memberships_and_explicit_unresolved_partition():
    validate_membership_projection(document())

@pytest.mark.parametrize('change',[{'role':'cosponsor'},{'areas':['CFPB']},{'withdrawn':True},
                                 {'bill':'H.R.2'},{'congress':'118'}])
def test_membership_cannot_override_adjudicated_scoring_fields(change):
    d=document();d['memberships'][0].update(change)
    with pytest.raises(ValueError):validate_membership_projection(d)

def test_missing_membership_cannot_silently_drop_credit():
    d=document();d['memberships']=[]
    with pytest.raises(ValueError,match='coverage'):validate_membership_projection(d)

def test_withdrawal_cannot_reappear_as_active():
    d=document();row=copy.deepcopy(d['memberships'][0]);row.update(bioguide='B',role='cosponsor')
    d['memberships'].append(row);d['unresolved_members']=[]
    with pytest.raises(ValueError,match='scoring fields'):validate_membership_projection(d)
