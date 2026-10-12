import json
import sqlite3

from fec.staffer_network.compact_existing_review import sha
from fec.staffer_network.integrate_target_history import build


def setup(tmp_path, amended_members):
    ledger = tmp_path / 'evidence.sqlite'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE person(id TEXT,label TEXT,canonical_id TEXT)')
        for number in (1,2):
            pid = 'person:lda:lobbyist:' + str(number)
            db.execute('INSERT INTO person VALUES(?,?,?)',(pid,'Person '+str(number),pid))
    def filing(uuid, posted, members):
        return {'filing_uuid':uuid,'dt_posted':posted,'filing_year':2018,'filing_period':'fourth_quarter',
                'registrant':{'id':1,'name':'Example Org'},'client':{'id':2},
                'filing_document_url':'https://lda.gov/filings/public/filing/'+uuid+'/print/',
                'lobbying_activities':[{'lobbyists':[{'lobbyist':{'id':n}} for n in members]}]}
    folder = tmp_path / 'A'; folder.mkdir()
    page = folder / 'page.json'
    page.write_text(json.dumps({'results':[filing('old','2019-01-01T00:00:00+00:00',[1,2]),
                                           filing('new','2020-01-01T00:00:00+00:00',amended_members)]}))
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({'status':'PASS','exact_uuid_and_payload_twins':True,
                                  'person_id':'lda:lobbyist:1','sources':[{'path':str(page),'sha256':sha(page)}]}))
    base = tmp_path / 'base.json'
    base.write_text(json.dumps({'status':'PASS','inputs':{'ledger':str(ledger),'ledger_sha256':sha(ledger)},
                               'nodes':[],'official_associations':[]}))
    return base, receipt


def test_amendment_removing_target_suppresses_older_membership(tmp_path):
    base, receipt = setup(tmp_path,[2])
    result = build(base, receipt, tmp_path/'candidate.json')
    assert result['route_groups'] == 0


def test_latest_version_is_cited_and_never_promotes_employment(tmp_path):
    base, receipt = setup(tmp_path,[1,2])
    output = tmp_path/'candidate.json'
    result = build(base,receipt,output)
    release = json.loads(output.read_bytes())
    assert result['route_groups'] == 1
    assert result['employment_promoted'] is False
    group = release['official_associations'][0]
    assert group['provenance']['activity_support'][0]['filing_uuid'] == 'new'
    assert group['relationship'] == 'historical_shared_lda_activity'
    assert all(n['operational_staffer_qualification'] is False for n in release['nodes'])
