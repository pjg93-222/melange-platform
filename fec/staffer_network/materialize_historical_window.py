"""Turn a twin-verified acquisition window into a sealed, source-only index.

No identity consolidation, employment promotion, or live publication occurs.
Unknown report-period encodings remain in the archive for explicit adjudication.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import time

from .lda_composite_repair import _atomic_json, _load_completed_window_result, compare_twins, _load_window_payloads
from .lda_source_native_corpus import _activity_id, _display_name, _source_id, _minimized_organization
from .reconcile_held_evidence import packed, sha, digest
from fec.surfaces.staffer_activity_index import report_bounds, build_period_memberships, SCHEMA


def materialize(root,window):
    root=Path(root).resolve();state=json.loads((root/'backfill_status.json').read_bytes())
    completed=state.get('completed',{}).get(window)
    if not completed or not completed['comparison']['pass']:raise ValueError('Window is not twin-verified')
    out=root/'normalized'/window;out.mkdir(parents=True,exist_ok=True)
    database=out/'activity.sqlite';receipt_path=out/'activity.sqlite.receipt.json'
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_bytes())
        building=out/'activity.building.sqlite'
        # Crash between sealing and install: finish the install only if the built file matches the seal.
        if not database.exists() and building.exists() and sha(building)==receipt['sha256']:building.replace(database)
        if not database.exists() or sha(database)!=receipt['sha256']:raise ValueError('Previously sealed historical index changed')
        return receipt
    if database.exists():raise ValueError('Historical index has no completed receipt')
    results=[];manifest_proofs=[]
    for twin in ('A','B'):
        path=root/'windows'/window/twin/'window_manifest.json';manifest=json.loads(path.read_bytes())
        spec={k:manifest[k] for k in ('window_id','filing_year','start_date','end_date_exclusive','max_in_flight_requests')}
        results.append(_load_completed_window_result(root,spec,twin))
        manifest_proofs.append({'path':str(path),'sha256':sha(path)})
    comparison=compare_twins(*results)
    if not comparison['pass'] or comparison['count_b']!=completed['records']:raise ValueError('Saved source reconciliation failed')
    payloads=_load_window_payloads(root,window,'B')
    temporary=out/'activity.building.sqlite'
    if temporary.exists():
        # Only this derived file, under this exact acquisition window, is restarted.
        temporary.unlink()
    con=sqlite3.connect(temporary);counts=Counter()
    con.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
      CREATE TABLE activity(id INTEGER PRIMARY KEY, source_id TEXT NOT NULL UNIQUE,start TEXT,end_exclusive TEXT,
        posted TEXT,filing_uuid TEXT,firm_id TEXT,issue TEXT,client_id TEXT);
      CREATE TABLE named(person TEXT NOT NULL,activity INTEGER NOT NULL,edge_id TEXT NOT NULL,PRIMARY KEY(person,activity)) WITHOUT ROWID;
      CREATE TABLE filing(uuid TEXT PRIMARY KEY,year INTEGER,period TEXT,posted TEXT,payload_sha256 TEXT,activity_count INTEGER);
      CREATE TABLE person_name(person TEXT,label TEXT,PRIMARY KEY(person,label)) WITHOUT ROWID;
      CREATE TABLE organization_observation(id TEXT PRIMARY KEY,organization TEXT,filing_uuid TEXT,payload TEXT);
      CREATE TABLE covered_position(id TEXT PRIMARY KEY,person TEXT,activity TEXT,filing_uuid TEXT,raw_text TEXT,date_semantics TEXT);
    ''')
    try:
        for uuid,filing in sorted(payloads.items()):
            activities=filing.get('lobbying_activities') or []
            if not isinstance(activities,list):raise ValueError('Malformed activity list')
            year=filing['filing_year'];period=filing.get('filing_period');posted=filing['dt_posted']
            con.execute('INSERT INTO filing VALUES(?,?,?,?,?,?)',(uuid,year,period,posted,digest(filing),len(activities)))
            counts['filings']+=1;counts['filings_without_activities']+=not activities
            start,end=report_bounds(year,period)
            counts['unrecognized_report_period']+=start is None
            orgs={}
            for kind in ('registrant','client'):
                row=_minimized_organization(filing[kind],kind=kind);orgs[kind]=row['source_org_id']
                con.execute('INSERT INTO organization_observation VALUES(?,?,?,?)',(digest([uuid,kind]),row['source_org_id'],uuid,packed(row)))
            for ordinal,activity in enumerate(activities):
                source_id=_activity_id(uuid,ordinal)
                cur=con.execute('INSERT INTO activity(source_id,start,end_exclusive,posted,filing_uuid,firm_id,issue,client_id) VALUES(?,?,?,?,?,?,?,?)',
                    (source_id,start,end,posted,uuid,orgs['registrant'],activity.get('general_issue_code'),orgs['client']))
                aid=cur.lastrowid;counts['activities']+=1
                seen=set()
                for item_ordinal,item in enumerate(activity.get('lobbyists') or []):
                    raw=item['lobbyist'];person=_source_id('lobbyist',raw['id']);label=_display_name(raw)
                    con.execute('INSERT OR IGNORE INTO person_name VALUES(?,?)',(person,label))
                    if person not in seen:
                        con.execute('INSERT INTO named VALUES(?,?,?)',(person,aid,'historical:edge:'+digest([source_id,person])))
                        seen.add(person);counts['named_on_activity']+=1
                    text=item.get('covered_position')
                    if text and str(text).strip():
                        con.execute('INSERT INTO covered_position VALUES(?,?,?,?,?,?)',
                            (digest([source_id,item_ordinal,person,text]),person,source_id,uuid,text,'UNPARSED_CAREER_ASSERTION_NOT_EMPLOYMENT'))
                        counts['covered_position_assertions']+=1
        con.execute('CREATE INDEX named_activity ON named(activity,person)')
        build_period_memberships(con)
        con.execute('CREATE INDEX covered_person ON covered_position(person)')
        con.execute('CREATE INDEX organization_source ON organization_observation(organization)')
        counts['source_person_ids']=con.execute('SELECT count(DISTINCT person) FROM person_name').fetchone()[0]
        counts['source_person_ids_with_name_variants']=con.execute('SELECT count(*) FROM (SELECT person FROM person_name GROUP BY person HAVING count(*)>1)').fetchone()[0]
        counts['period_memberships']=con.execute('SELECT count(*) FROM member').fetchone()[0]
        if counts['filings']!=completed['records']:raise ValueError('Filings lost during normalization')
        if con.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Historical SQLite integrity failure')
        con.commit()
    finally:con.close()
    receipt={'schema':SCHEMA,'status':'PASS','scope':'SOURCE_ONLY_HISTORICAL_WINDOW','window_id':window,
        'database':database.name,'sha256':sha(temporary),'bytes':temporary.stat().st_size,'counts':dict(counts),
        'sources':manifest_proofs,'source_receipt_sha256':digest(manifest_proofs),'builder_sha256':sha(Path(__file__)),
        'production_activated':False,'identity_adjudication_complete':False,'employment_inferred':False,
        'acquisition_comparison':comparison,'unknown_periods':'Retained without fabricated dates; absent from period routes.',
        'coverage_note':'This reconciles the API query result only; zero results do not establish the absence of historical lobbying.'}
    _atomic_json(receipt_path,receipt)  # seal first (atomic), then install; a crash in between is healed on rerun
    temporary.replace(database)
    return receipt


def watch(root):
    """Companion stage of the running acquisition, with a durable progress receipt."""
    root=Path(root).resolve();path=root/'normalization_status.json'
    status=json.loads(path.read_bytes()) if path.exists() else {'windows':{},'production_activated':False}
    def save():
        from .lda_composite_repair import _atomic_json
        from datetime import datetime,timezone
        status['updated_at']=datetime.now(timezone.utc).isoformat();_atomic_json(path,status)
    try:
        while True:
            source=json.loads((root/'backfill_status.json').read_bytes());status['status']='RUNNING'
            for window in source.get('completed',{}):
                if window not in status['windows']:
                    status['current_window']=window;save()
                    receipt=materialize(root,window)
                    status['windows'][window]={'counts':receipt['counts'],'database_sha256':receipt['sha256']}
                    print('NORMALIZED',window,receipt['counts'].get('filings',0),flush=True)
                    save()
            status['current_window']=None
            if source['status']!='RUNNING':
                status['status']='NORMALIZATION_PASS' if source['status']=='ACQUISITION_PASS' else 'ACQUISITION_NEEDS_ATTENTION'
                save();return status
            save();time.sleep(20)
    except Exception as exc:
        status.update(status='ATTENTION_REQUIRED',error=type(exc).__name__+': '+str(exc));save();raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    g=p.add_mutually_exclusive_group(required=True);g.add_argument('--window');g.add_argument('--watch',action='store_true')
    args=p.parse_args();print(packed(watch(args.root) if args.watch else materialize(args.root,args.window)))
