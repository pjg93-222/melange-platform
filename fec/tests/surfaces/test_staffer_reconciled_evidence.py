import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from fec.surfaces.staffer_reconciled_evidence import load_release, merge_bindings, augment_inventory, bind_client_groups, bind_official_associations
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.staffer_network.build_reconciliation_release import covered_support_check
from fec.surfaces.staffer_live_service import LiveService
from fec.tests.surfaces.test_staffer_ecosystem_routes import make_index, neighbors, record


def release(**fields):
    return {'status':'PASS','as_of':'2026-09-08','release_path':'fixture','release_sha256':'fixture',**fields}


def test_pin_requires_exact_verified_bytes(tmp_path):
    payload=tmp_path/'release.json';payload.write_text(json.dumps(release()))
    pin=tmp_path/'reconciliation_pin.json'
    pin.write_text(json.dumps({'status':'PASS','path':str(payload),'sha256':hashlib.sha256(payload.read_bytes()).hexdigest()}))
    assert load_release(tmp_path)['as_of']=='2026-09-08'
    payload.write_text(payload.read_text()+' ')
    with pytest.raises(ValueError,match='hash mismatch'):load_release(tmp_path)


def test_new_bindings_preserve_existing_work_records_and_explicit_separations():
    nodes={i:{'id':i,'label':i,'kind':'person'} for i in ('p','q','a','b','x')}
    original={'identity_aliases':{},'identity_groups':[], 'unresolved_candidates':[{'node_ids':['a','p']},{'node_ids':['q','p']}]}
    candidate=release(identity_bindings=[{'source':'a','target':'p'},{'source':'q','target':'p'},{'source':'b','target':'x'},{'source':'x','target':'p'}])
    result=merge_bindings(original,nodes,{},[],candidate,[('q','p'),('b','p')])
    assert result['identity_aliases']=={'a':'p','b':'x'}
    assert len(result['reconciled_evidence']['held'])==2
    assert original['identity_groups']==[]
    assert all(g['basis']=='RECORD_RESOLVED' for g in result['identity_groups'])
    assert all(g['status']!='USER_CONFIRMED_SAME_PERSON' for g in result['identity_groups'])
    assert result['unresolved_candidates']==[{'node_ids':['q','p']}]


def test_a_new_source_claim_cannot_overwrite_an_old_identity_binding():
    resolved={'identity_aliases':{},'identity_groups':[],'unresolved_candidates':[]}
    r=merge_bindings(resolved,{'p':{},'q':{}},{'a':'q'},[],release(identity_bindings=[{'source':'a','target':'p'}]))
    assert r['reconciled_evidence']['new_bindings']==0
    assert len(r['reconciled_evidence']['held'])==1


def test_roster_observations_do_not_replace_existing_employment_or_create_dates():
    existing={'p':{'id':'p','kind':'person','stints':[{'core_start':'2010-01-01'}],'employment_history':[{'title':'Counsel'}]}}
    result={'nodes':{}}
    augment_inventory(result,release(nodes=[{'id':'p','kind':'person','label':'P'}],person_evidence={'p':[{'date_description':'Observed in 2025','start':None,'end':None}]}),existing)
    assert result['nodes']['p']['stints']==existing['p']['stints']
    assert result['nodes']['p']['employment_history']==existing['p']['employment_history']
    assert result['nodes']['p']['reconciled_career_evidence'][0]['end'] is None
    assert 'reconciled_career_evidence' not in existing['p']


def test_roster_reuses_an_exact_spine_to_lda_link_instead_of_reintroducing_a_duplicate():
    existing={'person:hill:person:p':{'id':'person:hill:person:p','kind':'person','label':'Person P','lda_source_person_id':'lda:lobbyist:p'}}
    candidate=release(nodes=[{'id':'person:lda:lobbyist:p','kind':'person','label':'Person P'}],
        person_evidence={'person:lda:lobbyist:p':[{'title':'Director'}]},
        official_associations=[{'member_ids':['person:lda:lobbyist:p','person:other']}])
    result={'nodes':{}};augment_inventory(result,candidate,existing)
    assert set(result['nodes'])=={'person:hill:person:p'}
    assert result['nodes']['person:hill:person:p']['reconciled_career_evidence']==[{'title':'Director'}]
    index=SimpleNamespace(nodes=existing,to_node={'lda:lobbyist:p':'person:hill:person:p'})
    bind_official_associations(index,candidate)
    assert index.official_associations[0]['member_ids']==['person:hill:person:p','person:other']


def test_reconciled_client_ids_bridge_different_firms_in_same_period_only(tmp_path):
    index=make_index(tmp_path,[record('one','firm1','client1',['a']),record('two','firm2','client2',['b']),
        record('later','firm2','client2',['later'],4),record('other','firm3','different',['other'])])
    assert neighbors(index,'a')=={}
    bind_client_groups(index,release(client_groups=[{'primary_id':'client1','label':'Example Client','source_ids':['client1','client2'],'method':'REVIEWED_SOURCE_RECORDS'}]))
    rows=neighbors(index,'a')
    assert set(rows)=={'person:lda:lobbyist:b'}
    edge=rows['person:lda:lobbyist:b']
    assert edge['relationship']=='shared_client_period'
    assert edge['via_organization']['id']=='organization:client1'
    assert edge['via_organization']['label']=='Example Client'
    assert len(edge['provenance']['representative_period']['client_identity_evidence'])==2
    reverse=neighbors(index,'b')['person:lda:lobbyist:a']
    assert reverse['cost']==edge['cost']
    assert reverse['via_organization']==edge['via_organization']
    assert edge['continuous_employment_asserted'] is False


def test_official_roster_can_connect_non_lda_people_without_creating_coworkers(tmp_path):
    nodes={f'person:official:{p}':{'id':f'person:official:{p}','kind':'person','label':p} for p in ('a','b')}
    index=make_index(tmp_path,[],nodes)
    index.official_associations=[{'member_ids':list(nodes),'relationship':'official_team_roster','label':'Same official team roster',
        'organization_id':'org','organization_label':'Example','date_description':'September 2025 observation; duration unknown','cost':3,'provenance':{'url':'https://example.org/team'}}]
    router=AdvocacyRouter(nodes,[],'fixture','2026-09-08',activity_provider=index)
    a,b=nodes
    assert router.coworkers(a,{'include_review':True})['total']==0
    assert router.reported_collaborators(a,{'include_review':False})['total']==0
    result=router.route(a,b,{'include_review':True,'include_policy':False})
    assert result['status']=='found'
    hop=result['hops'][0]
    assert hop['start']==hop['end_exclusive']==''
    assert hop['temporal_status']=='source_observation'
    assert 'observed_period_count' not in hop
    assert router.route(b,a,{'include_review':True,'include_policy':False})['cost']==result['cost']


def test_client_reconciliation_does_not_resurrect_superseded_rosters(tmp_path):
    index=make_index(tmp_path,[record('old','firm','old-client',['a','removed'],posted='2026-04-19T00:00:00Z'),
        record('new','firm','new-client',['a','b']),record('tie1','other','old-client',['x','y']),
        record('tie2','other','new-client',['x','z'])])
    bind_client_groups(index,release(client_groups=[{'primary_id':'old-client','source_ids':['old-client','new-client'],'method':'REVIEWED'}]))
    assert set(neighbors(index,'a'))=={'person:lda:lobbyist:b'}
    assert neighbors(index,'removed')=={}
    assert neighbors(index,'x')=={}
    assert index.records('person:lda:lobbyist:removed')['total']==1


def test_future_reporting_periods_stay_in_archive_but_out_of_routes(tmp_path):
    index=make_index(tmp_path,[record('future','firm','client',['a','b'],7)])
    index.reconciliation_as_of='2026-06-01'
    assert neighbors(index,'a')=={}
    assert index.records('person:lda:lobbyist:a')['total']==1


def test_career_binding_vetoes_cross_job_and_historical_office_conflicts():
    proof={'office':'Sen. John Curtis','title':'Legislative Assistant','covered_dates':{'status':'MISSING_DATE_BOUND'}}
    reasons=covered_support_check('Senior Legislative Assistant, Rep. John Curtis',proof)
    assert 'HISTORICAL_CHAMBER_LABEL_CONFLICT' in reasons
    assert 'TITLE_IS_SUBSTRING_OF_DIFFERENT_ROLE' in reasons
    proof.update(office='Rep. A Smith',title='Legislative Director')
    assert 'MULTIPLE_OFFICES_IN_CLAUSE' in covered_support_check('Assistant, Rep. A Smith; Legislative Director, Rep. B Jones',proof)
    assert 'EXPLICIT_DATE_TEXT_REQUIRES_PARSING' in covered_support_check('Legislative Director, Rep. A Smith, March 2019 to February 2022',proof)
    assert covered_support_check('Legislative Director, Rep. A Smith',proof)==[]


def test_source_person_link_uses_adjudicated_identity_even_without_a_contact_match():
    service=object.__new__(LiveService);service.lock=threading.RLock()
    service.identity_aliases={'quorum:q':'person:p'}
    service.contacts=SimpleNamespace(resolve_source_person=lambda i:{'source_id':i,'person_id':None,'status':'source_only'})
    service.router=SimpleNamespace(nodes={'person:p':{'kind':'person'}},person_facts=SimpleNamespace(network_excluded=set()))
    result=service.staffer_person_link('quorum:q')
    assert result['person_id']=='person:p'
    assert result['status']=='matched_source_identity'
    assert result['route_available'] is True
    service.router.person_facts.network_excluded.add('person:p')
    assert service.staffer_person_link('quorum:q')['route_available'] is False
    assert service.staffer_person_link('quorum:unknown')['person_id'] is None
