"""Add explicitly labelled historical filing observations to ecosystem routes."""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from .compact_existing_review import packed, sha


def build(base_path, receipt_path, output, before_year=2019):
    base_path, receipt_path, output = map(Path, (base_path, receipt_path, output))
    if output.exists():
        raise ValueError('Immutable candidate exists')
    release = json.loads(base_path.read_bytes())
    receipt = json.loads(receipt_path.read_bytes())
    if release['status'] != 'PASS' or receipt['status'] != 'PASS' or not receipt['exact_uuid_and_payload_twins']:
        raise ValueError('Verified base and acquisition required')
    filings = {}
    for source in receipt['sources']:
        if sha(source['path']) != source['sha256']:
            raise ValueError('Source changed')
        if Path(source['path']).parent.name == 'A':
            for filing in json.loads(Path(source['path']).read_bytes())['results']:
                if filing['filing_year'] < before_year:
                    filings[filing['filing_uuid']] = (filing, source)
    # Select latest posted version before checking target membership: an amendment
    # removing a person must suppress the older roster containing that person.
    windows = defaultdict(list)
    for filing, source in filings.values():
        key = (filing['registrant']['id'], filing['client']['id'], filing['filing_year'], filing['filing_period'])
        windows[key].append((filing, source))
    ledger = Path(release['inputs']['ledger'])
    if sha(ledger) != release['inputs']['ledger_sha256']:
        raise ValueError('Ledger changed')
    with sqlite3.connect(ledger.resolve().as_uri() + '?mode=ro', uri=True) as db:
        persons = {p: (label, canonical) for p, label, canonical in db.execute('SELECT id,label,canonical_id FROM person')}
    target = receipt['person_id']
    nodes = {n['id']: n for n in release['nodes']}
    groups = {}
    held = []
    corroborations = []
    for versions in windows.values():
        latest_time = max(datetime.fromisoformat(f['dt_posted']) for f, _ in versions)
        newest = [(f,s) for f,s in versions if datetime.fromisoformat(f['dt_posted']) == latest_time]
        if len(newest) != 1:
            held.append({'reason':'AMBIGUOUS_LATEST_VERSION', 'filings':[f['filing_uuid'] for f,_ in newest]})
            continue
        filing, source = newest[0]
        for ordinal, activity in enumerate(filing['lobbying_activities']):
            raw_ids = {'lda:lobbyist:' + str(x['lobbyist']['id']) for x in activity['lobbyists']}
            if target not in raw_ids:
                continue
            members = []
            source_people = {'person:lda:lobbyist:' + str(x['lobbyist']['id']): x['lobbyist'] for x in activity['lobbyists']}
            for raw in sorted(raw_ids):
                pid = 'person:' + raw
                if pid not in persons:
                    raw_person = source_people[pid]
                    label = ' '.join(str(raw_person.get(part) or '') for part in ('first_name','middle_name','last_name','suffix')).strip()
                    label = ' '.join(label.split())
                    matching = {mid for group in release['official_associations']
                                if group.get('relationship') == 'official_reporting_line'
                                and group.get('organization_label','').casefold() == filing['registrant']['name'].casefold()
                                and str(filing['filing_year']) in group.get('date_description','')
                                for mid in group['member_ids']
                                if mid in nodes and nodes[mid].get('label','').casefold() == label.casefold()}
                    if len(matching) == 1:
                        canonical = next(iter(matching))
                        persons[pid] = (label, canonical)
                        persons.setdefault(canonical, (nodes[canonical]['label'], canonical))
                        corroborations.append({'source':pid,'target':canonical,
                            'method':'EXACT_FULL_NAME_ORGANIZATION_AND_CONTEMPORANEOUS_OFFICIAL_REPORTING_LINE',
                            'filing_uuid':filing['filing_uuid']})
                        nodes[canonical].setdefault('source_person_ids',[])
                        if raw not in nodes[canonical]['source_person_ids']:
                            nodes[canonical]['source_person_ids'].append(raw)
                        members.append(canonical)
                        continue
                    persons[pid] = (' '.join(label.split()), pid)
                    nodes[pid] = {'id':pid,'kind':'person','label':persons[pid][0],
                                  'ecosystem_only':True,'operational_staffer_qualification':False,
                                  'source_person_ids':[raw], 'name_status':'SOURCE_REPORTED',
                                  'provenance':{'method':'EXACT_LDA_SOURCE_ID_NO_CROSS_SOURCE_MERGE',
                                                'path':source['path'],'sha256':source['sha256'],
                                                'filing_uuid':filing['filing_uuid']}}
                label, canonical = persons[pid]
                if canonical not in persons:
                    canonical = pid
                nodes.setdefault(canonical, {'id':canonical,'kind':'person','label':persons[canonical][0],
                                             'ecosystem_only':True,'operational_staffer_qualification':False})
                members.append(canonical)
            members = sorted(set(members))
            if len(members) < 2:
                continue
            key = (filing['filing_uuid'], tuple(members))
            proof = {'filing_uuid':filing['filing_uuid'],'activity_ordinal':ordinal,
                     'issue':activity.get('general_issue_code'),'source':source}
            if key in groups:
                groups[key]['provenance']['activity_support'].append(proof)
                continue
            groups[key] = {
                'id':'historical:filing:' + hashlib.sha256(packed(key).encode()).hexdigest()[:24],
                'member_ids':members,'relationship':'historical_shared_lda_activity',
                'label':'Named on the same historical LDA activity',
                'organization_id':'organization:lda:registrant:' + str(filing['registrant']['id']),
                'organization_label':filing['registrant']['name'],
                'date_description':str(filing['filing_year']) + ' ' + filing['filing_period'].replace('_',' ') +
                    ': named on the same reported activity; not confirmed coworkers or continuous employment.',
                'cost':2,
                'provenance':{'url':filing['filing_document_url'],'path':source['path'],'sha256':source['sha256'],
                              'activity_support':[proof], 'version_policy':'Latest unique posted filing within the exact-person acquisition.'},
            }
    release['nodes'] = list(nodes.values())
    release['official_associations'].extend(groups.values())
    release['targeted_history'] = {'receipt':str(receipt_path.resolve()),'sha256':sha(receipt_path),
                                  'historical_filings':len(filings),'filing_periods':len(windows),
                                  'route_groups':len(groups),'held':held,'employment_promoted':False}
    release['targeted_history']['official_identity_corroborations'] = corroborations
    release['status']='CANDIDATE'
    release.pop('validation',None)
    output.write_text(json.dumps(release,indent=2)+'\n',encoding='utf8')
    return release['targeted_history']


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('base','receipt','output'): p.add_argument('--'+arg,required=True,type=Path)
    a=p.parse_args();print(json.dumps(build(a.base,a.receipt,a.output)))
