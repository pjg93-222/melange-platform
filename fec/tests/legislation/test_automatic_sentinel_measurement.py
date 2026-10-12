import copy
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
sys.path.insert(0,str(Path(__file__).parent))
from legislation import sentinel_admissions as SA
from legislation import official_membership_movements as OM
from legislation import refresh_sentinel_admissions as R
from legislation import sync_sentinel_bill_ledger as S
from legislation.congress_bill_repository import seed_fingerprint
from engine.primacy_factors import act_weight
from test_collector_roster_updates import _default_bundle,_repo_bill,_person,_repository,_pre_collapse_ledger,_build_root


URL='https://www.govinfo.gov/content/pkg/BILLS-119hr1919ih/xml/BILLS-119hr1919ih.xml'
QUOTE='A Federal credit union may invest in the obligations described in this section.'


def decision():
    return {'canonical_key':'119:hr:1919','disposition':'include','confidence':'high',
            'beneficiary_class':'cu_primary','rationale':'Explicit new investment power for federal credit unions.',
            'categories':[{'category':'Charter Modernization','direction':1,'rationale':'The operative provision broadens federal credit union investment powers.',
                           'source_url':URL,'quote':QUOTE}]}


@pytest.mark.parametrize('change',[{'confidence':'low'},{'canonical_key':'118:hr:1919'},{'categories':[]}])
def test_incomplete_or_wrong_identity_never_admitted(change):
    with pytest.raises(ValueError): R.validate_decision({**decision(),**change},'119:hr:1919',{URL:QUOTE},{'Charter Modernization'})


def test_invented_quote_and_boolean_direction_refused():
    d=decision();d['categories'][0]['quote']='Made up language about benefiting all credit unions.'
    with pytest.raises(ValueError): R.validate_decision(d,'119:hr:1919',{URL:QUOTE},{'Charter Modernization'})
    d=decision();d['categories'][0]['direction']=True
    with pytest.raises(ValueError): R.validate_decision(d,'119:hr:1919',{URL:QUOTE},{'Charter Modernization'})


def test_uncertainty_stays_hold_and_neutral_is_explicit():
    d=decision();d.update(disposition='hold',confidence='low',categories=[])
    assert R.validate_decision(d,'119:hr:1919',{},set())['disposition']=='hold'
    d=decision();d['categories'][0]['direction']=0
    assert R.validate_decision(d,'119:hr:1919',{URL:QUOTE},{'Charter Modernization'})['categories'][0]['direction']==0


def test_statutory_context_is_identity_checked():
    assert 'ceased' in R.statute_text(b'<title>12 USC 1786a: Omitted</title><p>ceased in 2001</p>',12,'1786a')
    with pytest.raises(ValueError): R.statute_text(b'<title>12 USC 1786: Other</title>',12,'1786a')


def test_seed_projection_updates_without_duplicates_or_unrelated_loss():
    bundle=_default_bundle();original=copy.deepcopy(bundle)
    bill=_repo_bill(_person('A000001','Alice Adams'))
    row={**bundle['members'][1]['bills'][0],'repository_key':bill['key'],'member':'Alice Adams','bioguide':'A000001',
         'role':'sponsor','withdrawn':False,'action':'A new official action'}
    d={'bills':{bill['key']:bill},'memberships':[row]}
    projected=SA.augment_bundle(bundle,d)
    assert projected['members'][1]['bills'][0]['action']=='A new official action'
    assert seed_fingerprint(projected)==seed_fingerprint(SA.augment_bundle(projected,d))
    assert bundle==original


def test_beneficiary_weight_uses_direction_without_inventing_endorsement():
    d={'bills':{'119:hr:1919':_repo_bill(_person('A000001','Alice Adams'))},'decisions':{'119:hr:1919':decision()}}
    tags=SA.effective_tags({},d);tag=tags['H.R.1919|119']
    assert tag['acu_position']=='' and act_weight(tag,'H.R.1919|119')==1
    d['decisions']['119:hr:1919']['categories'][0]['direction']=-1
    assert act_weight(SA.effective_tags({},d)['H.R.1919|119'],'H.R.1919|119')==-1
    ratified={'H.R.1919|119':{'class':'shared'}}
    assert SA.effective_tags(ratified,d)==ratified


def test_official_join_sponsor_and_withdrawal_flow_to_score_flags(tmp_path,monkeypatch):
    bundle=_default_bundle()
    bundle['members'][1]['bills']=[]
    repo=_repository([_repo_bill(_person('A000001','Alice Adams'),
           originals=[_person('B000002','Bob Brown',withdrawn='2026-09-01')],
           cosponsors=[_person('C000003','Cara Cole')])])
    movement=OM.build(bundle,repo,{'completed_at':datetime.now(timezone.utc).isoformat()},'a'*64,{})
    assert {r['role'] for r in movement['additions']}=={'sponsor','cosponsor'}
    assert [r['member'] for r in movement['facts'] if r['withdrawn']]==['Bob Brown']
    bills=_pre_collapse_ledger(bundle)
    assert OM.apply_to_desk(bills,movement)==1
    assert bills['Bob Brown']==[]
    updates=[{**r,'verified_at':'2026-09-08T00:00:00Z'} for r in movement['facts']]
    repo_path=tmp_path/'repo.json';repo_path.write_text(json.dumps(repo),encoding='utf-8')
    monkeypatch.setattr(S,'REPOSITORY',repo_path)
    plan=S.build_plan(repo,updates,[],official_movements=movement,input_path=tmp_path/'in.xlsx',output_path=tmp_path/'out.xlsx')
    rows=[dict(zip(plan['ledger_rows'][0],row)) for row in plan['ledger_rows'][1:] if row[0]=='membership']
    assert {r['Member']:r['Scoring Eligible'] for r in rows}=={'Alice Adams':True,'Bob Brown':False,'Cara Cole':True}
    assert any(r['sheet']=='Sponsor Interactions' for r in plan['accepted_interactions'])


def test_absence_does_not_invent_withdrawal():
    assert OM.eligible({'repository_key':'119:hr:1919','bioguide':'A000001','role':'sponsor'}, {})


def test_revised_held_measure_loses_credit_without_erasing_admission_history():
    bundle=_default_bundle();bill=_repo_bill(_person('A000001','Alice Adams'))
    document={'bills':{},'bill_catalog':{bill['key']:bill},'memberships':[],'decisions':{bill['key']:{'disposition':'hold'}}}
    projected=SA.augment_bundle(bundle,document)
    assert all(not m['bills'] for m in projected['members'])
    assert not SA.membership_eligible({'repository_key':bill['key'],'bioguide':'A000001','role':'sponsor'},document)
    assert bundle['members'][1]['bills']  # caller-owned history was not mutated


def test_classified_bill_without_current_roster_members_does_not_break_tag_closure():
    bill=_repo_bill(_person('A000001','Alice Adams'))
    document={'bills':{bill['key']:bill},'memberships':[],'decisions':{bill['key']:decision()}}
    assert SA.effective_tags({},document)=={}


def test_provider_failure_is_failed_receipt_and_keeps_last_good(tmp_path,monkeypatch):
    root=_build_root(tmp_path,_default_bundle(),_repository([_repo_bill(_person('A000001','Alice Adams'))]))
    directory=root/'data/legislation/congress_advanced_current';directory.mkdir()
    (directory/'congress_advanced_records.ndjson').write_text(json.dumps({'canonical_key':'119:hr:2000'})+'\n')
    registry=root/'source_data/legislation';registry.mkdir(parents=True)
    (registry/'acu_legislation_search_registry.json').write_text(json.dumps({'categories':[{'id':'payments'}]}))
    def fail(*args,**kwargs): raise RuntimeError('provider unavailable')
    monkeypatch.setattr(R,'collect',fail)
    assert R.main(['--root',str(root),'--keys','119:hr:2000'])==1
    assert not (root/SA.RELATIVE).exists()
    generation=next((root/'audits/sentinel_admissions').glob('*/generation.json'))
    assert json.loads(generation.read_text())['status']=='FAIL'


def test_automatic_cu_direction_does_not_claim_other_industry_valence(monkeypatch):
    from engine import primacy_factors as PF
    tag={'class':'cu_primary','authority':'automated-official-evidence-policy',
         'automated_category_directions':[-1],'acu_position':''}
    monkeypatch.setattr(PF,'ACT_W',{'cu_primary':0.15})
    monkeypatch.setattr(PF,'ACU_RELEVANCE_ACTIVE',False)
    assert PF.act_weight(tag,'H.R.1919|119')==0.15
