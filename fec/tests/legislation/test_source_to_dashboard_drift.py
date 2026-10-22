import json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from audit import audit_source_to_dashboard as audit

@pytest.fixture
def generation(tmp_path,monkeypatch):
    def write(name,data):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data),encoding='utf-8')
    doc={'memberships':[{'member':'A','bill':'H.R.10082','congress':'119','role':'sponsor','repository_key':'119:hr:10082'}],
         'decisions':{'119:hr:10082':{'disposition':'include','categories':[{'category':'Charter Modernization','direction':1}]}}}
    monkeypatch.setattr(audit.SA,'load',lambda root:doc)
    write(str(audit.SA.RELATIVE),{})
    write('member_desk_bundle.json',{'members':[{'name':'A','invested':120,'ind_money':0,'bills':[{'bill':'H.R.10082','congress':'119','role':'sponsor'}]}]})
    write('legislation_routing_audit.json',{'rows':[{'member':'A','bill':'H.R.10082','congress':'119','effective_category':'Charter Modernization','disposition':'selected','contribution':3}]})
    write('data/money/fec_refresh_current.json',{'checked_at':datetime.now(timezone.utc).isoformat(),'status':'PASS','cycle':2026,'latest_attributed_transaction_date':'2026-07-31','unresolved_members':[]})
    write('data/engine/congestion.json',[{'member':'A','cu':120,'receipts':2000}])
    write('data/money/fec_money_application.json',{'source_receipt_sha256':audit.sha(tmp_path/'data/money/fec_refresh_current.json'),'congestion_sha256':audit.sha(tmp_path/'data/engine/congestion.json')})
    write('dashboard_data.json',{'name':['A'],'rank':[1],'engine':{'cu':[120],'ind':[0],'rcp':[200000]}})
    write('ranks_python.json',{'rank':{'A':1}});write('archive_leg.json',{})
    return tmp_path,write,doc


def test_an_application_that_records_roster_drift_binds_on_its_hashes(generation):
    root,write,_=generation
    application={'source_receipt_sha256':audit.sha(root/'data/money/fec_refresh_current.json'),'congestion_sha256':audit.sha(root/'data/engine/congestion.json'),
                 'roster_drift':{'withheld':['B'],'dropped':['D']},
                 'identity_mapping_drift':{'collected_mapping_sha256':'1'*64,'candidate_ids_added_since':['S6XX00003']}}
    write('data/money/fec_money_application.json',application)
    assert audit.verify(root)['status']=='PASS'
    write('data/money/fec_money_application.json',{**application,'congestion_sha256':'0'*64})
    result=audit.verify(root)
    assert result['status']=='FAIL' and 'FEC application does not match current engine inputs' in result['errors']
