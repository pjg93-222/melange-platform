import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from audit import audit_measurement_layers as audit

@pytest.fixture
def generation(tmp_path,monkeypatch):
    # This connected-layer fixture contains one member and one seat. The
    # production 13-seat quota and infeasibility guard have their own tests.
    monkeypatch.setattr(audit.TU,'BUCKETS',(1,0,0))
    def write(path,value):
        target=tmp_path/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(value),encoding='utf-8')
    row={'member':'A','bill':'H.R.1','congress':119,'effective_category':'Payments',
         'effective_role':'sponsor','sign':1,'decay_weight':1,'contribution':3,
         'tracker_status':'Introduced','disposition':'selected'}
    repo={'119:hr:1':{'latest_action':{'text':'Introduced'}}}
    live=audit.measure(['A'],[row],repo,{'Payments':1},{'Payments':1},[.7,.3])
    live.update({'topic_weights':{'Payments':1},'efficiency_weights':{'Payments':1},'blend':[.7,.3]})
    write('ranks_python.json',{'rank':{'A':1},'score':{'A':.5},'pcts':{'A':{'leg':0,'press':1,'inf':1,'dem':0}},
                             'ab_weights':{'2':.45,'3':.1,'4':.4,'5':.05},'legislative_pillar':live})
    write('legislation_routing_audit.json',{'rows':[row]});write('archive_leg.json',{'A':{'Payments':3}})
    write('bill_repository_live.json',{'bills':repo});write('vote_category_lean.json',{'votes':{},'name_of':{}})
    binding={p:audit.sha(tmp_path/p) for p in ('archive_leg.json','legislation_routing_audit.json','bill_repository_live.json','vote_category_lean.json')}
    write('category_stance.json',{'cats':['Payments'],'defensive':[],'matrix':{'A':{'Payments':'Champion'}},
        'scores':{'A':{'Payments':1}},'raw_scores':{'A':{'Payments':1}},'signal':{'A':{'Payments':True}},
        'rounding':{'factors':{'A':{'Payments':0}}},'source_contract':{'schema':'adjudicated-label-inputs-v1','inputs_sha256':binding}})
    desk={'cats':['Payments'],'tiername':{'C':'Champion','A':'Ally'},'members':[
        {'name':'A','rank':1,'score':.5,'tier':1,'tierrank':1,'leg_raw':0,'leg_n':0,'press_n':1,'comm_n':1,'demo_n':0,
         'overall_stance':'C','stances':'C','bills':[{'bill':'H.R.1','congress':'119','role':'sponsor'}]}]}
    write('member_desk_bundle.json',desk)
    tag={'bill':'H.R.1','congress':'119','class':'cu_primary'}
    (tmp_path/'bill_beneficiary_tags.ndjson').write_text(json.dumps(tag)+'\n')
    factors,baseline=audit.PF.compute(desk,{'H.R.1|119':tag})
    constants={'roster_baseline':baseline}
    if audit.PF.PRIMACY_T1_MIN_AFFIRMATIVE > 0:
        constants['affirmative_primary_credit_policy']=audit.PF.AFFIRMATIVE_PRIMARY_POLICY
    write('data/engine/primacy_factors.json',{'members':factors,'_constants':constants})
    monkeypatch.setattr(audit.SA,'load',lambda root:{'bills':{},'memberships':[]})
    write('dashboard_data.json',{'name':['A'],'rank':[1],'tier':[1],'tierrank':[1],'gen':['C'],'stance':['C'],
        'engine':{'categories':['Payments'],'tierp':[audit.MC.primacy_engine_value(1)],
            'priority':[[audit.MC.blended_engine_priority(1,{'Payments':1},'Payments')]],
            'orientation':[[audit.MC.engine_orientation({'Payments':1},'Payments')]]}})
    write('data/lobbying/industry_tiers.json',{'industries':{'credit_union':{'rows':[{'member':'A','tier':1,'tierrank':1}]}}})
    # D-031 (2026-09-17): the SHIPPED board is per_area_v1 and tier_util fails
    # LOUD on a missing per-area grade rather than collapsing back to the factor
    # board (tier_util.py:624-630). audit_measurement_layers seats the board at
    # :120, so the fixture must carry the artifact the shipped board reads or
    # the connected-generation case raises before a single layer is compared.
    # One member, so grade_percentile is the single-member percentile 1.0.
    write('data/engine/per_area_grade.json',
          {'schema':'per-area-grade-v1',
           'members':{'A':{'chamber':'House','grade_percentile':1.0}}})
    return tmp_path,write

def test_connected_generation_passes(generation):
    root,_=generation
    result=audit.verify(root)
    assert result['status']=='PASS',result['errors']

@pytest.mark.parametrize('kind',['score','raw','label','priority','primacy','affirmative','source'])
def test_stale_or_corrupt_layer_refuses_publication(generation,kind):
    root,write=generation
    if kind=='source':
        write('archive_leg.json',{'A':{'Payments':2}})
    elif kind=='primacy':
        path='data/engine/primacy_factors.json';d=audit.read(root/path);d['members']['A']['factor_raw']=.2;write(path,d)
    elif kind=='affirmative':
        if audit.PF.PRIMACY_T1_MIN_AFFIRMATIVE == 0:
            pytest.skip('The historical dial omits affirmative credit.')
        path='data/engine/primacy_factors.json';d=audit.read(root/path)
        # Valid-looking credit preserves this member's seat and every score;
        # the audit must still reject evidence unsupported by the source acts.
        d['members']['A'][audit.PF.AFFIRMATIVE_PRIMARY_FIELD]=2.0;write(path,d)
    elif kind=='score':
        path='ranks_python.json';d=audit.read(root/path);d['score']['A']=.7;write(path,d)
    elif kind=='raw':
        path='member_desk_bundle.json';d=audit.read(root/path);d['members'][0]['leg_raw']=.9;write(path,d)
    else:
        path='dashboard_data.json';d=audit.read(root/path)
        if kind=='label':d['stance']=['A']
        else:d['engine']['priority']=[[.1]]
        write(path,d)
    result=audit.verify(root)
    assert result['status']=='FAIL' and result['errors']
