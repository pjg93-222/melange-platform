"""Integrate source-reported career clauses confirmed by targeted filing history."""
from collections import defaultdict
from datetime import datetime
import argparse
import gzip
import json
from pathlib import Path
import re
import sqlite3

from .compact_existing_review import sha


def build(base_path, cases_receipt, backfill_manifest, output):
    base_path,cases_receipt,backfill_manifest,output=map(Path,(base_path,cases_receipt,backfill_manifest,output))
    if output.exists():raise ValueError('Immutable candidate exists')
    release=json.loads(base_path.read_bytes()); cases=json.loads(cases_receipt.read_bytes())
    manifest=json.loads(backfill_manifest.read_bytes())
    if release['status']!='PASS' or cases['status']!='PASS' or manifest['status'] not in ('PASS','PARTIAL'):
        raise ValueError('Completed inputs required')
    entry=next(x for x in cases['files'] if Path(x['path']).name=='supported_clauses.ndjson.gz')
    if sha(entry['path'])!=entry['sha256']:raise ValueError('Case evidence changed')
    with gzip.open(entry['path'],'rt',encoding='utf8') as stream: rows=[json.loads(x) for x in stream]
    support=defaultdict(list); labels={}; inputs=[]
    for completed in manifest['completed']:
        receipt_path=Path(completed['receipt']); receipt=json.loads(receipt_path.read_bytes())
        if receipt['status']!='PASS' or not receipt['exact_uuid_and_payload_twins']:
            raise ValueError('Unverified target')
        inputs.append({'path':str(receipt_path),'sha256':sha(receipt_path)})
        windows=defaultdict(list)
        for source in receipt['sources']:
            if sha(source['path'])!=source['sha256']:raise ValueError('Filing payload changed')
            if Path(source['path']).parent.name!='A':continue
            for filing in json.loads(Path(source['path']).read_bytes())['results']:
                key=(filing['registrant']['id'],filing['client']['id'],filing['filing_year'],filing['filing_period'])
                windows[key].append((filing,source))
        for versions in windows.values():
            latest=max(datetime.fromisoformat(f['dt_posted']) for f,_ in versions)
            selected=[(f,s) for f,s in versions if datetime.fromisoformat(f['dt_posted'])==latest]
            if len(selected)!=1:continue
            filing,source=selected[0]
            for ordinal,activity in enumerate(filing['lobbying_activities']):
                for item in activity['lobbyists']:
                    raw=item['lobbyist']; person='lda:lobbyist:'+str(raw['id'])
                    if person!=receipt['person_id']:continue
                    labels[person]=' '.join(' '.join(str(raw.get(p) or '') for p in ('first_name','middle_name','last_name','suffix')).split())
                    for clause in re.split(r'[;\n]+',item.get('covered_position') or ''):
                        support[person,clause.strip()].append({'filing_uuid':filing['filing_uuid'],
                            'activity_ordinal':ordinal,'url':filing['filing_document_url'],**source})
    ledger=Path(release['inputs']['ledger'])
    if sha(ledger)!=release['inputs']['ledger_sha256']:raise ValueError('Ledger changed')
    with sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True) as db:
        people={p:(label,canonical) for p,label,canonical in db.execute('SELECT id,label,canonical_id FROM person')}
    nodes={n['id']:n for n in release['nodes']}; added=0; held=[]
    for row in rows:
        proofs=support.get((row['person'],row['raw_text'].strip()),[])
        if not proofs or len(row['employer_ids'])!=1:
            held.append({'clause_id':row['clause_id'],'person':row['person'],
                         'reason':'NO_EXACT_CLAUSE_IN_LATEST_TARGET_FILINGS_OR_AMBIGUOUS_OFFICE'})
            continue
        pid='person:'+row['person']
        if pid not in people:
            held.append({'clause_id':row['clause_id'],'person':row['person'],
                         'reason':'SOURCE_PERSON_NOT_IN_ACCEPTED_POPULATION'})
            continue
        label,canonical=people[pid]
        nodes.setdefault(canonical,{'id':canonical,'label':label,'kind':'person','ecosystem_only':True,
                                    'operational_staffer_qualification':False,'name_status':'SOURCE_REPORTED',
                                    'source_person_ids':[row['person']]})
        assessment=row['assessment']; date_text=assessment.get('source_date_text') or assessment.get('matched_date_text')
        if not date_text:raise ValueError('Source date precision missing')
        observations=release['person_evidence'].setdefault(canonical,[])
        if any(o.get('raw_text','').strip()==row['raw_text'].strip() for o in observations):continue
        observations.append({'title':row['title_labels'][0],'organization':row['employer_labels'][0],
            'raw_text':row['raw_text'],'status':'SOURCE_REPORTED_CAREER_CLAIM','start':None,'end':None,
            'continuous_employment_asserted':False,'source_date_assessment':assessment,
            'date_description':'LDA covered-position claim: '+date_text+'. Source precision retained; employment is not independently confirmed.',
            'source':{'label':'LDA filing','url':'https://lda.gov/filings/public/filing/'+proofs[0]['filing_uuid']+'/print/',
                      'path':proofs[0]['path'],'sha256':proofs[0]['sha256'],'filing_support':proofs}})
        added+=1
    release['nodes']=list(nodes.values());release['status']='CANDIDATE';release.pop('validation',None)
    release['backfilled_career_integration']={'observations_added':added,'held':held,'inputs':inputs,
        'cases_receipt':str(cases_receipt),'cases_receipt_sha256':sha(cases_receipt),
        'employment_promoted':False,'source_native_ecosystem_ids_only':True}
    output.write_text(json.dumps(release,indent=2)+'\n',encoding='utf8')
    return {'observations_added':added,'held':len(held),'status':'CANDIDATE'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('base','cases','manifest','output'):p.add_argument('--'+key,required=True,type=Path)
    a=p.parse_args();print(json.dumps(build(a.base,a.cases,a.manifest,a.output)))
