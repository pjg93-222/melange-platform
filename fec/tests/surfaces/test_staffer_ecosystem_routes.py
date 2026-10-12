import json
import math
import sqlite3
import time

from fec.surfaces.staffer_activity_index import ActivityIndex, SCHEMA, build_period_memberships, sha
from fec.surfaces.staffer_routing import AdvocacyRouter


def make_index(tmp_path, records, nodes=None):
    db = tmp_path / 'activity.sqlite'
    con = sqlite3.connect(db)
    con.executescript('''CREATE TABLE activity(id INTEGER PRIMARY KEY,source_id TEXT,start TEXT,end_exclusive TEXT,posted TEXT,filing_uuid TEXT,firm_id TEXT,issue TEXT,client_id TEXT);
      CREATE TABLE named(person TEXT,activity INTEGER,edge_id TEXT);''')
    for ident, (filing, firm, client, start, end, posted, people) in enumerate(records, 1):
        con.execute('INSERT INTO activity VALUES(?,?,?,?,?,?,?,?,?)',(ident,f'act:{ident}',start,end,posted,filing,firm,'TAX',client))
        for person in people:
            con.execute('INSERT INTO named VALUES(?,?,?)',('lda:lobbyist:'+person,ident,f'edge:{ident}:{person}'))
    build_period_memberships(con)
    con.commit();con.close()
    db.with_suffix('.sqlite.receipt.json').write_text(json.dumps({'schema':SCHEMA,'status':'PASS','sha256':sha(db),'bytes':db.stat().st_size,'source_receipt_sha256':'fixture'}))
    if nodes is None:
        nodes={f'person:lda:lobbyist:{p}':{'kind':'person','label':p} for r in records for p in r[-1]}
    return ActivityIndex(db,nodes)


def record(filing,firm,client,people,quarter=1,posted='2026-04-20T00:00:00Z'):
    return filing,firm,client,f'2026-{quarter:02}-01',f'2026-{quarter+3:02}-01',posted,people


def neighbors(index,person):
    return index.neighbors('person:lda:lobbyist:'+person,AdvocacyRouter._options({'include_review':True,'max_neighbor_checks':1000000,'timeout_ms':10000}),{'deadline':time.monotonic()+10,'neighbor_checks':0})


def test_specificity_repetition_and_period_gaps(tmp_path):
    index=make_index(tmp_path,[record('f1','firm1','client1',['a','b']),record('f1','firm1','client1',['a','b']),
        record('f1','firm1','client1',['c']),record('f2','firm1','client2',['d']),record('f3','firm2','client1',['e']),
        record('f4','firm3','client3',['unrelated']),record('f5','firm1','client1',['a','b'],7),
        record('f6','firm1','client1',['nonoverlap'],4)])
    rows=neighbors(index,'a')
    assert set(rows)=={'person:lda:lobbyist:'+p for p in 'bcde'}
    assert [rows['person:lda:lobbyist:'+p]['relationship'] for p in 'bcde']==[
        'co_reported_lobbying_activity','co_reported_filing','co_reported_registrant_period','shared_client_period']
    assert rows['person:lda:lobbyist:b']['observed_period_count']==2
    assert rows['person:lda:lobbyist:b']['continuous_employment_asserted'] is False
    assert rows['person:lda:lobbyist:e']['via_organization']['id']=='organization:client1'
    for other, edge in rows.items():
        reverse=neighbors(index,other.removeprefix('person:lda:lobbyist:'))['person:lda:lobbyist:a']
        assert (edge['cost'],edge['relationship'],edge['observed_period_count'])==(reverse['cost'],reverse['relationship'],reverse['observed_period_count'])


def test_superseded_and_conflicting_versions_do_not_create_routes(tmp_path):
    index=make_index(tmp_path,[record('old','firm1','client1',['a','removed'],posted='2026-04-19T00:00:00Z'),
        record('new','firm1','client1',['a','b']),record('tie1','firm2','client2',['a','x']),record('tie2','firm2','client2',['a','y'])])
    assert set(neighbors(index,'a'))=={'person:lda:lobbyist:b'}
    assert index.records('person:lda:lobbyist:a')['total']==4


def test_reference_aliases_retain_evidence_both_directions_without_name_merges(tmp_path):
    nodes={'person:lda:lobbyist:a':{'kind':'person','label':'Person'},
        'person:lda:lobbyist:primary':{'kind':'person','label':'Gregory','identity_status':'REFERENCE_RESOLVED_SAME_PERSON',
          'identity_basis':'REFERENCE_RESOLVED','source_person_ids':['lda:lobbyist:primary','lda:lobbyist:alias']},
        'person:lda:lobbyist:same_name':{'kind':'person','label':'Gregory'}}
    index=make_index(tmp_path,[record('f','firm','client',['a','alias'])],nodes)
    assert set(neighbors(index,'a'))=={'person:lda:lobbyist:primary'}
    assert set(neighbors(index,'primary'))=={'person:lda:lobbyist:a'}
    assert neighbors(index,'same_name')=={}
    assert index.records('person:lda:lobbyist:primary')['total']==1


def test_router_can_cross_ecosystem_but_does_not_turn_it_into_work_overlap(tmp_path):
    index=make_index(tmp_path,[record('f','firm','client',['a']),record('f','firm','client',['b'])])
    router=AdvocacyRouter(index.nodes,[],'test','2026-09-08',activity_provider=index)
    opts={'include_review':True,'include_policy':False}
    assert router.coworkers('person:lda:lobbyist:a',opts)['total']==0
    assert router.reported_collaborators('person:lda:lobbyist:a',opts)['total']==1
    router.nodes['person:lda:lobbyist:b']['name_status']='UNRESOLVED'
    # Eligibility is enforced by the router, outside the source membership index.
    assert router._person_allowed('person:lda:lobbyist:b',{**AdvocacyRouter._options(opts),'include_unresolved':False}) is False
    assert router.reported_collaborators('person:lda:lobbyist:a',opts)['total']==0


def test_many_clients_scan_a_firm_quarter_once(tmp_path):
    index=make_index(tmp_path,[record(f'f{i}','firm',f'client{i}',['a','b']) for i in range(200)])
    budget={'deadline':time.monotonic()+10,'neighbor_checks':0}
    options=AdvocacyRouter._options({'include_review':True,'max_neighbor_checks':2000})
    result=index.neighbors('person:lda:lobbyist:a',options,budget)
    assert len(result)==1
    assert result['person:lda:lobbyist:b']['observed_period_count']==1
    assert budget['neighbor_checks']<1200


def test_mixed_route_prefers_filing_then_verified_work_over_weaker_client_bridge(tmp_path):
    index=make_index(tmp_path,[record('f1','firm1','client1',['a']),record('f1','firm1','client1',['b']),record('f2','firm2','client1',['c'])])
    episodes=[{'person_id':'person:lda:lobbyist:'+p,'employer_id':'office:verified','start':'2026-09-08',
        'end_exclusive':'2026-09-09','as_of':'2026-09-08','current_evidence':True} for p in 'bc']
    router=AdvocacyRouter(index.nodes,episodes,'fixture','2026-09-08',activity_provider=index)
    result=router.route('person:lda:lobbyist:a','person:lda:lobbyist:c',{'include_review':True,'include_policy':False})
    assert result['status']=='found'
    assert result['cost']==4
    assert [h['relationship'] for h in result['hops']]==['co_reported_filing','current_coworker']
