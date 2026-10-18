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


def test_complete_source_to_dashboard(generation):
    root,_,_=generation
    assert audit.verify(root)['status']=='PASS'


@pytest.mark.parametrize('stale',[False,True])
def test_headline_rank_must_use_fresh_legislative_pillar(generation,stale):
    root,write,_=generation
    write('data/engine/sentinel_admission_policy.json',{'live_legislative_pillar':True})
    live={'pillar':{'A':0},'inputs_sha256':{'archive_leg.json':audit.sha(root/'archive_leg.json')}}
    write('data/engine/live_legislative_pillar.json',live)
    write('ranks_python.json',{'rank':{'A':1},'legislative_pillar':live,'pcts':{'A':{'leg':1 if stale else 0}}})
    assert audit.verify(root)['status']==('FAIL' if stale else 'PASS')


def test_invalid_candidate_cannot_replace_the_served_dashboard(generation):
    from surfaces.sync_live_dashboard_alias import sync
    root,write,_=generation
    (root/'Moneyball.html').write_text('new candidate',encoding='utf-8')
    (root/'Moneyball_Redesign.html').write_text('last good',encoding='utf-8')
    data=audit.read(root/'dashboard_data.json');data['rank']=[2];write('dashboard_data.json',data)
    with pytest.raises(ValueError,match='publication refused'):sync(root)
    assert (root/'Moneyball_Redesign.html').read_text(encoding='utf-8')=='last good'
    data['rank']=[1];write('dashboard_data.json',data)
    assert sync(root)['prepublication_source_guard']=='PASS'
    assert (root/'Moneyball_Redesign.html').read_text(encoding='utf-8')=='new candidate'

def test_clean_workspace_shell_is_current_only_while_bound_to_this_generation(generation):
    from surfaces import sync_live_dashboard_alias as alias
    root,_,_=generation
    (root/'Moneyball.html').write_text('generated',encoding='utf-8')
    builder=root/alias.CLEAN_BUILDER;builder.parent.mkdir(parents=True)
    builder.write_text("from pathlib import Path\ndef main():(Path(__file__).resolve().parents[2]/'Moneyball_Redesign.html').write_bytes(b'<!-- melange-clean-workspace-v2 --> rebuilt')\n",encoding='utf-8')
    (root/'Moneyball_Redesign.html').write_bytes(b'<!-- melange-clean-workspace-v2 --> old')
    stale='The dashboard served by the local preview is stale'
    assert stale in audit.verify(root)['errors']
    assert alias.sync(root)['mode']==alias.CLEAN_MODE
    assert audit.verify(root)['status']=='PASS'
    (root/'lobby-bills.html').write_text('a newer generation',encoding='utf-8')
    result=audit.verify(root)
    assert result['status']=='FAIL' and stale in result['errors']

@pytest.mark.parametrize('kind',['rank','money','industry','neutral'])
def test_stale_or_wrong_downstream_measurements_fail(generation,kind):
    root,write,doc=generation
    if kind=='neutral':doc['decisions']['119:hr:10082']['categories'][0]['direction']=0
    else:
        d=audit.read(root/'dashboard_data.json')
        if kind=='rank':d['rank']=[2]
        elif kind=='industry':d['engine']['ind']=[100]
        else:d['engine']['cu']=[0]
        write('dashboard_data.json',d)
    result=audit.verify(root)
    assert result['status']=='FAIL' and result['errors']


def _restamp(root,write,checked_at,status='PASS'):
    # rewrite the FEC source receipt and re-bind the application to it, so only the receipt's age/status differs
    write('data/money/fec_refresh_current.json',{'checked_at':checked_at.isoformat(),'status':status,'cycle':2026,'latest_attributed_transaction_date':'2026-07-31','unresolved_members':[]})
    write('data/money/fec_money_application.json',{'source_receipt_sha256':audit.sha(root/'data/money/fec_refresh_current.json'),'congestion_sha256':audit.sha(root/'data/engine/congestion.json')})


@pytest.mark.parametrize('days,label',[(1,'FRESH'),(20,'STALE_LABELLED')])
def test_fec_money_past_its_window_is_served_labelled_not_failed(generation,days,label):
    # D-212 tiered degrade: a verified last-good FEC generation past its 8-day window is a labelled warning, not a failure
    root,write,_=generation
    _restamp(root,write,datetime.now(timezone.utc)-timedelta(days=days))
    result=audit.verify(root)
    assert result['status']=='PASS' and result['money_freshness']['status']==label
    assert bool(result['warnings'])==(label=='STALE_LABELLED')


@pytest.mark.parametrize('kind',['future','failed','unbound'])
def test_future_dated_failed_or_unbound_fec_money_still_fails(generation,kind):
    root,write,_=generation
    if kind=='future':_restamp(root,write,datetime.now(timezone.utc)+timedelta(hours=2))
    elif kind=='failed':_restamp(root,write,datetime.now(timezone.utc)-timedelta(days=20),status='FAIL')
    else:
        _restamp(root,write,datetime.now(timezone.utc)-timedelta(days=20))
        write('data/money/fec_money_application.json',{'source_receipt_sha256':'0'*64,'congestion_sha256':audit.sha(root/'data/engine/congestion.json')})
    result=audit.verify(root)
    assert result['status']=='FAIL' and result['errors']
