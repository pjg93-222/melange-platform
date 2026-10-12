import gzip
import json
import sqlite3

import pytest

from fec.staffer_network.compact_existing_review import sha
from fec.staffer_network.integrate_backfilled_careers import build


@pytest.mark.parametrize('latest_text,expected', [('Counsel, Example Office 2010-2012',1),('',0)])
def test_latest_filing_must_still_contain_exact_career_clause(tmp_path, latest_text, expected):
    ledger=tmp_path/'ledger.sqlite'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE person(id TEXT,label TEXT,canonical_id TEXT)')
        db.execute("INSERT INTO person VALUES('person:lda:lobbyist:1','Test Person','person:lda:lobbyist:1')")
    base=tmp_path/'base.json'
    base.write_text(json.dumps({'status':'PASS','inputs':{'ledger':str(ledger),'ledger_sha256':sha(ledger)},'nodes':[],'person_evidence':{}}))
    folder=tmp_path/'A';folder.mkdir()
    raw='Counsel, Example Office 2010-2012'
    def filing(ident,posted,text):
        return {'filing_uuid':ident,'dt_posted':posted,'filing_year':2018,'filing_period':'first_quarter',
                'registrant':{'id':1},'client':{'id':1},'filing_document_url':'https://lda.gov/'+ident,
                'lobbying_activities':[{'lobbyists':[{'lobbyist':{'id':1,'first_name':'Test','last_name':'Person'},'covered_position':text}]}]}
    page=folder/'page.json';page.write_text(json.dumps({'results':[filing('old','2018-04-01T00:00:00Z',raw),filing('new','2018-05-01T00:00:00Z',latest_text)]}))
    receipt=tmp_path/'target.json';receipt.write_text(json.dumps({'status':'PASS','exact_uuid_and_payload_twins':True,'person_id':'lda:lobbyist:1','sources':[{'path':str(page),'sha256':sha(page),'url':'https://lda.gov/api/v1/filings/'}]}))
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'status':'PASS','completed':[{'receipt':str(receipt)}]}))
    source=tmp_path/'supported_clauses.ndjson.gz'
    with gzip.open(source,'wt',encoding='utf8') as f:
        f.write(json.dumps({'person':'lda:lobbyist:1','clause_id':'c1','raw_text':raw,'employer_ids':['office:1'],'title_labels':['Counsel'],'employer_labels':['Example Office'],'assessment':{'matched_date_text':'2010-2012'}})+'\n')
    cases=tmp_path/'cases.json';cases.write_text(json.dumps({'status':'PASS','files':[{'path':str(source),'sha256':sha(source)}]}))
    output=tmp_path/'out.json'
    result=build(base,cases,manifest,output)
    assert result['observations_added']==expected
    if expected:
        claim=json.loads(output.read_bytes())['person_evidence']['person:lda:lobbyist:1'][0]
        assert claim['start'] is None and claim['end'] is None
        assert claim['source']['filing_support'][0]['filing_uuid']=='new'
