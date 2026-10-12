"""Run Define -> Collect -> Identify -> Connect -> Count -> Verify/report.

Reads a quiescent Mentat database, reuses indexed FEC evidence, automatically
recovers a bounded amount of missing evidence, and writes an auditable ledger.
No source database or accepted workbook is modified. Unresolved cases remain
visible; completion of this computation is not certification of unique dollars.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

from cross_reference_digest import (LARGE_MEDIAN_MULTIPLE, LARGE_PERCENTILE, build_digest,
                                    multiple_setting, percentile_setting)
from cross_reference_store import EvidenceStore, canonical, fetch_exact_evidence, fetch_filing_header_evidence, now
from report_source_guard import atomic, cents, source_sha, sha, accepted_scopes, accepted_facts
from validate_industry_coverage import api_query_coverage
from recheck_attribution import recheck_attribution

PROJECT = Path(__file__).resolve().parents[1]
STEPS = [('define','Define'),('collect','Collect'),('identify','Identify'),('connect','Connect'),('count','Count'),('verify','Verify and report')]
# Which counted receipts the dwarfed cross-reference view lists for an eyeball.
# The cut is relative to the run: a row is listed when it reaches this
# percentile of its own class and this multiple of that class's median. These
# settings select rows to look at; they change no accounting decision.
LARGE_RECEIPT_VARIABLES = (('MENTAT_XREF_LARGE_PERCENTILE',LARGE_PERCENTILE,float,percentile_setting),
                           ('MENTAT_XREF_LARGE_MEDIAN_MULTIPLE',LARGE_MEDIAN_MULTIPLE,int,multiple_setting))


def outlier_rule(environment=None):
    """Read the percentile and median multiple from the environment, refusing anything else."""
    environment=os.environ if environment is None else environment
    values=[]
    for name,default,parse,validate in LARGE_RECEIPT_VARIABLES:
        raw=str(environment.get(name) or '').strip()
        if not raw:values.append(default);continue
        try:value=parse(raw)
        except ValueError:value=raw
        values.append(validate(value,name))
    return tuple(values)


def table_exists(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())


def baseline_rows(root, industry, cycles):
    scopes=accepted_scopes(root,industry,cycles)
    if not scopes:return {},None
    result={}
    for sid,raw,analysis in accepted_facts(scopes):
        if str(sid) in result:raise ValueError('Accepted cycles contain conflicting copies of a source ID')
        result[str(sid)]={'raw':json.loads(raw),'analysis':json.loads(analysis) if analysis else {}}
    info={'sources':scopes,'basis':'accepted_report_receipts'}
    if len(scopes)==1:info.update(snapshot=scopes[0]['snapshot'],sha256=scopes[0]['snapshot_sha256'])
    return result,info


def read_sources(database,industry,cycles,baseline,companies=None,baseline_pinned=False):
    rows=[];conflicts=[]
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        for table,period in [('contributions','two_year_transaction_period'),('corporate_giving','cycle')]:
            if not table_exists(db,table):continue
            query=f'SELECT * FROM {table} WHERE industry=?'
            params=[industry]
            if companies:
                query+=f' AND company IN ({",".join("?" for _ in companies)})';params+=list(companies)
            for item in db.execute(query,params):
                row=dict(item);sid=str(row['sub_id']);row['sub_id']=sid;row['source_table']=table
                row['source_query_cycle']=row.get(period)
                try:
                    receipt_date=date.fromisoformat(str(row.get('contribution_receipt_date') or '')[:10])
                except ValueError as error:
                    raise ValueError(f'FEC record {sid} has no valid receipt date; resolve its reporting scope first') from error
                row['cycle']=receipt_date.year+receipt_date.year%2
                if row['cycle'] not in cycles:continue
                prior=baseline.get(sid)
                eligible=(str(row.get('company_match_status','accepted')).lower()=='accepted' and not row.get('excluded_reason'))
                row['current_receipt_cents']=int(prior['analysis'].get('counted_cents',0)) if prior else (0 if baseline_pinned else (cents(row['contribution_receipt_amount']) if eligible and not row.get('duplicate_memo') else 0))
                row['baseline_company']=row['company']
                row['attribution_claims']=[];row['source_conflicts']=[]
                if prior:
                    row['baseline_party']=prior['analysis'].get('party')
                    row['baseline_category']=prior['analysis'].get('recipient_category')
                rows.append(row)
        by_id=defaultdict(list)
        for row in rows:by_id[row['sub_id']].append(row)
        if table_exists(db,'attribution_claims'):
            for item in db.execute('SELECT * FROM attribution_claims'):
                claim=dict(item)
                for row in by_id.get(str(claim.get('sub_id')),[]):row['attribution_claims'].append(claim)
        if table_exists(db,'attribution_conflicts'):
            for item in db.execute('SELECT * FROM attribution_conflicts'):
                conflict=dict(item)
                for row in by_id.get(str(conflict.get('sub_id')),[]):
                    row['attribution_claims'].append({'company':conflict.get('incoming_company'),'status':'competing_legacy_claim'})
        if table_exists(db,'source_record_conflicts'):
            for item in db.execute('SELECT * FROM source_record_conflicts'):
                conflict=dict(item)
                for row in by_id.get(str(conflict.get('sub_id')),[]):row['source_conflicts'].append(conflict)
        roster=sorted({r['company'] for r in rows})
        if not companies and table_exists(db,'companies'):
            roster=sorted({r[0] for r in db.execute('SELECT DISTINCT company FROM companies WHERE industry=?',(industry,))})
        coverage=api_query_coverage(db,roster,cycles,industry)
    return rows,roster,coverage


def compare_source_facts(rows, baseline):
    """Separate source loss/corrections from accounting changes, including net-zero edits."""
    current={};changes=[]
    for row in rows:
        sid=row['sub_id']
        facts={k:row.get(k) or '' for k in ('sub_id','committee_id','contributor_name')}
        facts.update(date=str(row.get('contribution_receipt_date') or '')[:10],
                     amount_cents=cents(row.get('contribution_receipt_amount')),cycle=row['cycle'])
        if sid in current and current[sid]!=facts:
            raise ValueError(f'Conflicting source facts for FEC ID {sid}')
        current[sid]=facts
    for sid in sorted(set(current)|set(baseline)):
        before=baseline.get(sid,{}).get('raw');after=current.get(sid)
        if before != after:
            changes.append({'source_id':sid,'kind':'added' if before is None else 'missing' if after is None else 'changed',
                            'before':before,'after':after})
    return changes


def coalesce_source_observations(rows):
    """One physical FEC ID can be returned by several queries; retain all claims.

    Query rejection (e.g. a person found by an organization-name search) does
    not override an accepted employer claim. Competing accepted identities or
    accounting facts remain unresolved, and every physical input stays saved.
    """
    compare_source_facts(rows,{})
    groups=defaultdict(list)
    for row in rows:groups[row['sub_id']].append(row)
    result=[]
    accounting_fields=('transaction_id','file_num','filing_form','line_number','entity_type','contributor_id',
                       'memo_text','memo_code','memoed_subtotal','receipt_type','receipt_type_full',
                       'back_reference_transaction_id','back_reference_schedule_name',
                       'conduit_committee_id','contributor_employer','contributor_zip')
    def present(value):return value not in (None,'')
    for sid, observations in sorted(groups.items()):
        priority={'accepted':0,'review':1,'rejected':2,'excluded':3}
        ordered=sorted(observations,key=lambda r:(priority.get(str(r.get('company_match_status') or '').lower(),4),
                        bool(r.get('excluded_reason')),str(r.get('company')),str(r['source_table']),canonical(r)))
        merged=dict(ordered[0]);merged['source_observation_count']=len(observations)
        merged['source_tables']=sorted({r['source_table'] for r in observations})
        claims=[];conflicts=[]
        for row in ordered:
            claims.extend(row.get('attribution_claims') or [])
            claims.append({key:row.get(key) for key in ('company','industry','company_match_status','company_match_reason','excluded_reason','source_table','phase','matched_variant')})
            conflicts.extend(row.get('source_conflicts') or [])
            for key,value in row.items():
                if not present(merged.get(key)) and present(value) and key not in ('excluded_reason','company_match_status','company_match_reason'):
                    merged[key]=value
        for field in accounting_fields:
            # An explicitly blank memo flag means non-memo, unlike absent/null
            # metadata. Preserve that disagreement with an observed X flag.
            values={canonical(r[field]) for r in ordered if present(r.get(field)) or
                    (field in ('memo_code','memoed_subtotal') and field in r and r[field] is not None)}
            if len(values)>1:conflicts.append({'source_id':sid,'reason':'stored_metadata_observation_conflict','field':field,'values':sorted(values)})
        baselines={r['current_receipt_cents'] for r in ordered}
        # A pinned baseline is one physical-ID decision. Without a pin, the
        # previous receipt estimate includes the eligible observation once.
        merged['current_receipt_cents']=max(baselines,key=abs)
        if len({v for v in baselines if v})>1:
            raise ValueError(f'Conflicting baseline amounts for FEC ID {sid}')
        merged['attribution_claims']=[json.loads(s) for s in sorted({canonical(c) for c in claims})]
        merged['source_conflicts']=[json.loads(s) for s in sorted({canonical(c) for c in conflicts})]
        result.append(merged)
    return result


def write_ledger(directory,rows,result,evidence,headers,scope,source_conflicts,store=None):
    path=directory/'ledger.sqlite'
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''
          CREATE TABLE source_records(id INTEGER PRIMARY KEY,source_id TEXT,source_table TEXT,payload_json TEXT);
          CREATE INDEX source_record_lookup ON source_records(source_id);
          CREATE TABLE decisions(id INTEGER PRIMARY KEY,source_id TEXT,payload_json TEXT);
          CREATE INDEX decision_lookup ON decisions(source_id);
          CREATE TABLE links(id INTEGER PRIMARY KEY,payload_json TEXT);
          CREATE TABLE changes(id INTEGER PRIMARY KEY,source_id TEXT,payload_json TEXT);
          CREATE TABLE review_queue(id INTEGER PRIMARY KEY,payload_json TEXT);
          CREATE TABLE coverage_queue(id INTEGER PRIMARY KEY,payload_json TEXT);
          CREATE TABLE evidence(source_id TEXT PRIMARY KEY,payload_json TEXT);
          CREATE TABLE filing_headers(file_num TEXT PRIMARY KEY,payload_json TEXT);
          CREATE TABLE evidence_documents(sha256 TEXT PRIMARY KEY,source_url TEXT,observed_at TEXT,payload_json TEXT);
          CREATE TABLE evidence_observations(observation_id TEXT PRIMARY KEY,payload_json TEXT);
          CREATE TABLE run_metadata(name TEXT PRIMARY KEY,payload_json TEXT);
        ''')
        db.executemany('INSERT INTO source_records(source_id,source_table,payload_json) VALUES(?,?,?)',
                       ((r['sub_id'],r['source_table'],canonical(r)) for r in rows))
        for table in ('decisions','links','changes','review_queue','coverage_queue'):
            for item in result.get(table,[]):
                if table in ('decisions','changes'):
                    db.execute(f'INSERT INTO {table}(source_id,payload_json) VALUES(?,?)',(item.get('source_id'),canonical(item)))
                else:db.execute(f'INSERT INTO {table}(payload_json) VALUES(?)',(canonical(item),))
        db.executemany('INSERT INTO evidence VALUES(?,?)',((sid,canonical(e)) for sid,e in sorted(evidence.items())))
        db.executemany('INSERT INTO filing_headers VALUES(?,?)',((file,canonical(h)) for file,h in sorted(headers.items())))
        if store is not None:
            documents={identifier for d in result['decisions'] for identifier in d['evidence_ids'] if re.fullmatch('[a-f0-9]{64}',identifier)}
            identifiers=sorted({r['sub_id'] for r in rows})
            for start in range(0,len(identifiers),500):
                batch=identifiers[start:start+500]
                documents.update(r[0] for r in store.db.execute(
                    'SELECT DISTINCT document_sha256 FROM records WHERE source_id IN ('+','.join('?' for _ in batch)+')',batch))
            for file in headers:
                found=store.db.execute('SELECT document_sha256 FROM headers WHERE file_num=?',(file,)).fetchone()
                if found:documents.add(found[0])
            for identifier in sorted(documents):
                document=store.db.execute('SELECT sha256,source_url,observed_at,payload_json FROM documents WHERE sha256=?',(identifier,)).fetchone()
                if document is None:continue # supplied assertion IDs can name an external evidence package
                if hashlib.sha256(document[3].encode('utf-8')).hexdigest()!=identifier:
                    raise ValueError('Archived evidence document failed hash verification')
                db.execute('INSERT INTO evidence_documents VALUES(?,?,?,?)',document)
                cursor=store.db.execute('SELECT * FROM document_observations WHERE document_sha256=?',(identifier,))
                keys=[column[0] for column in cursor.description]
                for observed in cursor:
                    value=dict(zip(keys,observed))
                    db.execute('INSERT INTO evidence_observations VALUES(?,?)',(value['observation_id'],canonical(value)))
        for key,value in {'scope':scope,'checks':result.get('checks'), 'totals':result.get('totals'), 'source_conflicts':source_conflicts}.items():
            db.execute('INSERT INTO run_metadata VALUES(?,?)',(key,canonical(value)))
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Decision ledger failed SQLite integrity check')
    return path


def source_ids(item):
    return [str(i) for i in (item.get('source_ids') or [item.get('source_id') or item.get('record_id')]) if i]


def coverage_presentation(result, rows):
    """Keep reusable filing checks separate from decisions about observed receipts.

    A missing complete filing is one collection task, not a request to manually
    adjudicate each otherwise classified receipt in that filing. Associated
    gross amounts are exposure, never an error estimate or a giving subtotal.
    """
    amounts = {r['sub_id']: cents(r['contribution_receipt_amount']) for r in rows}
    groups = defaultdict(lambda: {'ids': set(), 'actions': set()})
    tasks = []
    for item in result.get('coverage_queue', []):
        ids = set(source_ids(item))
        group = groups[item['reason']]
        group['ids'].update(ids)
        group['actions'].add(item['action'])
        tasks.append({**{key: item.get(key, '') for key in
                         ('reason', 'action', 'committee_id', 'file_num', 'root_file_num')},
                      'records': len(ids), 'amount': sum(abs(amounts[s]) for s in ids) / 100})
    reasons = [{'reason': reason, 'records': len(group['ids']),
                'amount': sum(abs(amounts[s]) for s in group['ids']) / 100,
                'action': '; '.join(sorted(group['actions']))} for reason, group in groups.items()]
    return sorted(reasons, key=lambda r: (-r['amount'], r['reason'])), sorted(tasks, key=canonical)


def presentation(result,rows,evidence=None):
    decisions=result.get('decisions',[]);by_id={r['sub_id']:r for r in rows}
    evidence=evidence or {}
    groups=defaultdict(lambda:{'records':set(),'positive':0,'negative':0})
    review_ids=set();review=[]
    for item in result.get('review_queue',[]):
        ids=source_ids(item);reason=item.get('reason','Evidence needs review')
        group=groups[reason]
        for sid in ids:
            row=by_id.get(sid,{})
            value=cents(row.get('contribution_receipt_amount'))
            if sid not in group['records']:
                group['positive']+=max(0,value);group['negative']+=min(0,value);group['records'].add(sid)
            review_ids.add(sid)
            review.append({'record_id':sid,'reason':reason,'company':row.get('company',''),'cycle':row.get('cycle'),
                           'amount':value/100,'action':item.get('action','Review supporting FEC evidence'),
                           'source_url':row.get('pdf_url') or ''})
    # A high-dollar record with several questions occupies one priority slot.
    priority={}
    for item in review:
        entry=priority.setdefault(item['record_id'],{**item,'reasons':set(),'actions':set()})
        entry['reasons'].add(item['reason']);entry['actions'].add(item['action'])
    review=[{**{k:v for k,v in r.items() if k not in ('reasons','actions')},
             'reason':'; '.join(sorted(r['reasons'])),'action':'; '.join(sorted(r['actions']))} for r in priority.values()]
    review.sort(key=lambda r:(-abs(r['amount']),r['record_id']))
    changes=[]
    for decision in decisions:
        sid=str(decision['source_id']);row=by_id.get(sid,{})
        before=int(decision.get('baseline_cents',row.get('current_receipt_cents',0)))
        after=int(decision.get('counted_cents',0))
        if before!=after:
            described={**row,**{k:v for k,v in evidence.get(sid,{}).items() if v is not None}}
            source_description='; '.join(str(described.get(k) or '').strip() for k in ('receipt_type_full','memo_text') if str(described.get(k) or '').strip())[:500]
            changes.append({'record_id':sid,'company':row.get('company',''),'cycle':row.get('cycle'),
                            'reason':'; '.join(map(str,decision.get('reasons',[]))) or decision.get('reason',decision.get('status','')),
                            'before':before/100,'after':after/100,'delta':(after-before)/100,
                            'source_url':row.get('pdf_url') or '', 'source_description':source_description})
    changes.sort(key=lambda r:(-abs(r['delta']),r['record_id']))
    # Engine supplies all integer-cent metrics; this adapter never certifies them.
    totals=result.get('totals',{})
    original=int(totals['provisional_original_giving_cents'])
    baseline=int(totals['baseline_receipt_cents'])
    buckets=totals['by_bucket']
    def bucket(name):return int(buckets.get(name,{}).get('counted_cents',0))
    positive=sum(max(0,cents(by_id[sid].get('contribution_receipt_amount'))) for sid in review_ids if sid in by_id)
    negative=sum(min(0,cents(by_id[sid].get('contribution_receipt_amount'))) for sid in review_ids if sid in by_id)
    coverage_ids = {sid for item in result.get('coverage_queue', []) for sid in source_ids(item)}
    summary={'input_records':len(rows),'automatic_decisions':sum(str(d['source_id']) not in review_ids for d in decisions),
             'source_verified_records':sum(d.get('receipt_verification_status') == 'source_verified' for d in decisions),
             'classified_from_stored_records':sum(d.get('receipt_verification_status') == 'classified_from_stored_evidence' for d in decisions),
             'coverage_records':len(coverage_ids),'coverage_only_records':len(coverage_ids - review_ids),
             'coverage_amount':sum(abs(cents(by_id[s]['contribution_receipt_amount'])) for s in coverage_ids)/100,
             'unresolved_records':len(review_ids),'unresolved_amount':(positive+abs(negative))/100,
             'unresolved_positive':positive/100,'unresolved_negative':negative/100,
             'observed_original_giving':original/100,'baseline_amount':baseline/100,
             'amount_change':(original-baseline)/100,'verified_unique':False,
             'employee_giving':bucket('employee_original')/100,
             'corporate_giving':bucket('corporate_original')/100,
             'unverified_funding':sum(bucket(k) for k in ('organization_unverified','funding_unverified','conflicting_source_evidence'))/100,
             'linked_refund_adjustments':bucket('linked_refund')/100,
             'pac_outgoing':int(totals['pac_activity_observed_on_recipient_receipts_cents'])/100,
             'supported_original_giving':int(totals['supported_unique_gross_cents'])/100}
    reason_groups=[{'reason':key,'records':len(value['records']),'amount':(value['positive']+abs(value['negative']))/100}
                   for key,value in groups.items()]
    return summary,sorted(reason_groups,key=lambda r:(-r['amount'],r['reason'])),review,changes


def partisan_breakdowns(result,rows):
    """Reuse the accepted recipient classification with an explicit denominator."""
    by_id={r['sub_id']:r for r in rows};groups=defaultdict(lambda:Counter())
    for d in result['decisions']:
        row=by_id[d['source_id']];party=str(row.get('baseline_party') or '').upper()
        party='D' if party=='D' else 'R' if party=='R' else 'other_unknown'
        for view,amount in [('original_giving',d['counted_cents']),('pac_activity',d['pac_activity_cents'])]:
            groups[view][party]+=amount;groups[view]['gross']+=abs(amount)
            if party in ('D','R'):groups[view]['classified_gross']+=abs(amount)
    result=[]
    for view,values in sorted(groups.items()):
        d,r=values['D'],values['R'];denominator=d+r
        valid=denominator>0 and d>=0 and r>=0
        result.append({'view':view,'democratic':d/100,'republican':r/100,
            'other_unknown':values['other_unknown']/100,'denominator_dr':denominator/100,
            'democratic_percent':100*d/denominator if valid else None,
            'republican_percent':100*r/denominator if valid else None,
            'classified_gross_percent':100*values['classified_gross']/values['gross'] if values['gross'] else None,
            'basis':'Accepted recipient party classification; D/(D+R) of signed receipts. Other/unknown stays separate; gross coverage uses absolute amounts. This does not assign untraced intermediary money to its final recipients.'})
    return result


COMMITTEE_MASTER_CYCLES=('cm22','cm24','cm26')


def committee_master_paths(root=None):
    root=Path(root or PROJECT/'inputs/fec_committee_master')
    return [root/cycle/'cm.txt' for cycle in COMMITTEE_MASTER_CYCLES]


def company_names(database,industry):
    """Every name the source roster and the verified employer aliases give a listed company."""
    names=defaultdict(set)
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if table_exists(db,'companies'):
            for company,variant in db.execute('SELECT company,variant FROM companies WHERE industry=?',(industry,)):
                if company and variant:names[str(company)].add(str(variant))
    aliases=PROJECT/'inputs/verified_employer_aliases.csv'
    if aliases.is_file():
        with aliases.open(encoding='utf-8-sig',newline='') as stream:
            for item in csv.DictReader(stream):
                if item.get('company') and item.get('alias'):names[item['company']].add(item['alias'])
    return {company:sorted(values) for company,values in sorted(names.items())}


def committee_connected_organizations(root=None):
    """FEC committee master field 14 (CONNECTED_ORG_NM) by committee ID, across the cycles on disk."""
    names=defaultdict(set)
    for path in committee_master_paths(root):
        if not path.is_file():continue
        with path.open(encoding='latin-1') as stream:
            for line in stream:
                fields=line.rstrip('\r\n').split('|')
                if len(fields)>=14 and fields[0].strip() and fields[13].strip():
                    names[fields[0].strip().upper()].add(fields[13].strip())
    return {committee:sorted(values) for committee,values in sorted(names.items())}


def excluded_reasons(result):
    """Rows and gross dollars removed per reason; a removed row lists every reason it carries."""
    groups=defaultdict(lambda:{'records':0,'amount_cents':0})
    for decision in result.get('decisions',[]):
        if decision.get('status')!='excluded_with_evidence':continue
        amount=abs(int(decision.get('amount_cents',0)))
        for reason in decision.get('reasons',[]):
            groups[reason]['records']+=1;groups[reason]['amount_cents']+=amount
    return sorted(({'reason':reason,'records':value['records'],'amount':value['amount_cents']/100} for reason,value in groups.items()),
                  key=lambda r:(-r['amount'],r['reason']))


def run(database,industry,cycles=(2022,2024,2026),state_root=None,baseline_root=None,evidence_dirs=(),
        header_dirs=(),fetch=True,max_requests=100,companies=None,affiliations_path=None,manifest_path=None,progress_callback=None):
    from cross_reference_engine import analyze
    database=Path(database).resolve();cycles=sorted(set(map(int,cycles)))
    if not cycles or any(c<2000 or c%2 for c in cycles):raise ValueError('Use even election cycles')
    # Read before any work so a mistyped setting fails now, not after the run.
    large_percentile,large_median_multiple=outlier_rule()
    state_root=Path(state_root or os.environ.get('MENTAT_CROSS_REFERENCE_ROOT',PROJECT/'state/cross-reference')).resolve()
    baseline_root=Path(baseline_root or os.environ.get('MENTAT_ACCEPTED_SOURCE_ROOT',PROJECT/'state/accepted-report-sources'))
    if affiliations_path is None:
        default_affiliations = PROJECT / 'inputs/verified_company_affiliations.json'
        affiliations_path = default_affiliations if default_affiliations.is_file() else None
    state_root.mkdir(parents=True,exist_ok=True)
    run_id=time.strftime('%Y%m%dT%H%M%S',time.gmtime())+'-'+uuid.uuid4().hex[:12]
    directory=state_root/run_id;directory.mkdir()
    started=time.monotonic()
    steps=[{'id':key,'label':label,'status':'pending','detail':''} for key,label in STEPS]
    def update(step,status,detail):
        item=next(s for s in steps if s['id']==step);item.update(status=status,detail=detail)
        value={'schema_version':1,'run_id':run_id,'status':'running','updated_at':now(),'steps':steps}
        atomic(directory/'progress.json',value)
        if progress_callback:progress_callback(value)
    atomic(state_root/'latest.json',{'schema_version':1,'run_id':run_id})
    store=None
    try:
        update('define','running','Pinning companies, cycles and source database')
        source_hash=source_sha(database)
        baseline,baseline_info=baseline_rows(baseline_root,industry,cycles)
        if companies:baseline={sid:b for sid,b in baseline.items() if b['analysis'].get('company') in companies}
        rows,roster,coverage=read_sources(database,industry,cycles,baseline,companies,baseline_info is not None)
        source_changes=compare_source_facts(rows,baseline) if baseline_info else []
        atomic(directory/'source-comparison.json',{'baseline':baseline_info,'changes':source_changes})
        losses=[c for c in source_changes if c['kind'] in ('missing','changed')]
        if losses:raise ValueError(f'{len(losses)} accepted source records are missing or financially changed. See source-comparison.json; resolve the source discrepancy before publishing accounting totals.')
        if not rows:raise ValueError('No source records match the requested scope')
        source_observations=rows
        rows=coalesce_source_observations(rows)
        rule_paths=[Path(__file__).with_name(name+'.py') for name in ('run_cross_reference','cross_reference_engine','cross_reference_store',
                    'pac_evidence','attribution_policy','report_source_guard','validate_industry_coverage','fec_filing_headers','recheck_attribution')]+[
                    PROJECT/'config/attribution-policy.json',
                    PROJECT/'inputs/fec_receipt_rules.json', PROJECT/'tools/recheck_attribution.R',
                    *[PROJECT/'R'/name for name in ('paths.R','schema.R','ingest.R','fec_page.R','collect.R')],
                    PROJECT/'inputs/company_match_overrides.csv', PROJECT/'inputs/company_home_states.csv',
                    PROJECT/'inputs/verified_employer_aliases.csv']
        fingerprints={str(p.relative_to(PROJECT)):sha(p) for p in rule_paths if p.exists()}
        for relative in fingerprints:
            target=directory/'rules'/relative;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((PROJECT/relative).read_bytes())
        # The committee master decides the recipient route's connected-organization
        # test; it is fingerprinted, not copied (three multi-megabyte FEC bulk files).
        committee_master={str(p.relative_to(PROJECT)):sha(p) for p in committee_master_paths() if p.is_file()}
        supplied={key:{'sha256':sha(Path(p)),'path':str(Path(p).resolve())} for key,p in [('affiliations',affiliations_path),('filing_manifest',manifest_path)] if p}
        for key,item in supplied.items():(directory/(key+'.json')).write_bytes(Path(item['path']).read_bytes())
        scope={'industries':[industry],'companies':roster,'cycles':cycles,'source_count':1,
               'database':str(database),'database_sha256':source_hash,'baseline':baseline_info,
               'rule_fingerprints':fingerprints,'supplied_evidence':supplied,'committee_master':committee_master,
               'source_change_counts':dict(Counter(c['kind'] for c in source_changes)),
               'source_observations':len(source_observations),'distinct_source_ids':len(rows),
               'accounting_basis':'original giving once; PAC outgoing activity separately','collection_coverage':coverage,
               'cycle_basis':'calendar receipt date; original source query period preserved separately',
               'cutoff':'source snapshot; later filings and unlisted aliases may add evidence'}
        update('define','completed',f'{len(roster)} companies; {len(cycles)} cycles; immutable source pinned')
        update('collect','running','Reusing saved source responses and filing headers')
        store=EvidenceStore(state_root/'evidence.sqlite')
        imports=[]
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:store.import_database(db)
        for path in [*evidence_dirs,*header_dirs]:imports.append(store.import_directory(path))
        recovery={'requested':0,'recovered':0,'failures':[]}
        header_recovery={'requested':0,'recovered':0,'failures':[]}
        if fetch and max_requests:
            budget=Path(os.environ.get('MENTAT_API_BUDGET_DB',PROJECT/'.cache/fec-api-budget.sqlite'));budget.parent.mkdir(parents=True,exist_ok=True)
            candidates=[r for r in rows if str(r.get('company_match_status') or '').lower()=='accepted' and not r.get('excluded_reason')]
            def header_progress(done,total,recovered):
                update('collect','running',f'Cross-checking filing headers: {done}/{total}; {recovered} recovered')
            header_recovery=fetch_filing_header_evidence(store,candidates,max_requests//4,state_root/'filing-headers',budget,header_progress)
            def recovery_progress(done,total,recovered):
                update('collect','running',f'Cross-checking exact FEC records: {done}/{total}; {recovered} recovered')
            recovery=fetch_exact_evidence(store,candidates,max_requests-header_recovery['requested'],state_root/'fec-api',budget,recovery_progress)
        evidence,headers,conflicts=store.resolve(rows)
        by_id=defaultdict(list)
        for row in rows:by_id[row['sub_id']].append(row)
        for conflict in conflicts:
            for row in by_id.get(conflict['source_id'],[]):row['source_conflicts'].append(conflict)
        for row in rows:
            h=headers.get(str(row.get('file_num')))
            if h and h['committee_id']==row.get('committee_id') and not row.get('filing_form'):
                row['filing_form']=h['filing_form']
        update('collect','completed' if coverage['complete'] else 'review',
               f'{len(evidence)} exact source records cross-checked; '+('query coverage passed' if coverage['complete'] else 'historical query coverage remains unproven'))
        update('identify','running','Resolving donor identity, funding source and receipt meaning')
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
            has_roster = table_exists(db, 'companies')
        attribution_recheck = recheck_attribution(rows, database, directory, evidence) if has_roster else {
            'status': 'unavailable', 'resolved_records': 0, 'reason': 'Source has no company roster'}
        scope['attribution_recheck'] = attribution_recheck
        affiliations=json.loads(Path(affiliations_path).read_text()) if affiliations_path else None
        filing_manifest=json.loads(Path(manifest_path).read_text()) if manifest_path else None
        update('connect','running','Cross-referencing explicit filing, memo and intermediary relationships')
        names=company_names(database,industry)
        connected=committee_connected_organizations()
        scope['attribution_text_test']={'companies_with_names':len(names),'committees_with_connected_organization':len(connected)}
        result=analyze(rows,evidence=evidence,headers=headers,affiliations=affiliations,filing_manifest=filing_manifest,
                       company_names=names,committee_connected_orgs=connected)
        update('identify','review' if result.get('review_queue') else 'completed','Attribution and transaction decisions recorded with evidence')
        update('connect','review' if result.get('review_queue') or result.get('coverage_queue') else 'completed',
               f'{len(result.get("links",[]))} evidence-backed links; filing coverage checks tracked separately from receipt review')
        update('count','running','Reconciling original giving and separate PAC activity in integer cents')
        summary,reasons,review,changes=presentation(result,rows,evidence)
        coverage_reasons,coverage_tasks=coverage_presentation(result,rows)
        update('count','completed','Observed accounting views computed; unique-dollar certification remains separate')
        update('verify','running','Checking monetary reconciliation and source preservation')
        if source_sha(database)!=source_hash:raise ValueError('Source changed during cross-referencing')
        if any(sha(PROJECT/path)!=expected or sha(directory/'rules'/path)!=expected for path,expected in fingerprints.items()):
            raise ValueError('Accounting rules changed during the run; repeat with stable code')
        if any(sha(PROJECT/path)!=expected for path,expected in committee_master.items()):
            raise ValueError('Committee master changed during the run; repeat with stable inputs')
        if any(sha(Path(value['path']))!=value['sha256'] or sha(directory/(key+'.json'))!=value['sha256'] for key,value in supplied.items()):
            raise ValueError('Supplied evidence changed during the run')
        if any(sha(directory/name) != expected for name,expected in attribution_recheck.get('artifacts', {}).items()):
            raise ValueError('Attribution evidence changed during the run')
        checks=result.get('checks',{})
        invariants=('integer_cent_reconciliation','unique_source_ids','relationship_graph_acyclic',
                    'duplicate_exclusions_have_retained_representation','original_and_pac_buckets_disjoint')
        if any(checks.get(key) is not True for key in invariants):raise ValueError('Cross-reference engine integrity checks failed')
        # Reconcile each decision, not only the net report change.
        for d in result.get('decisions',[]):
            if not isinstance(d.get('counted_cents'),int):raise ValueError('Accounting decisions must use integer cents')
        ledger=write_ledger(directory,source_observations,result,evidence,headers,scope,conflicts,store)
        update('verify','review' if review or coverage_tasks or not coverage['complete'] else 'completed',
               'Source unchanged; decisions reconciled; receipt review and collection coverage reported separately')
        report={'schema_version':1,'run_id':run_id,'generated_at':now(),'status':'completed','scope':scope,
                'summary':summary,'steps':steps,'unresolved_reasons':reasons,'excluded_reasons':excluded_reasons(result),
                'review_queue':review[:20],'changes':changes[:20],
                'coverage_reasons':coverage_reasons,'coverage_task_count':len(coverage_tasks),
                'partisan_breakdowns':partisan_breakdowns(result,rows),
                'recovery':recovery,'header_recovery':header_recovery,'imports':imports,'checks':checks,'link_count':len(result.get('links',[])),
                'attribution_recheck':attribution_recheck,
                'ledger_sha256':sha(ledger),'elapsed_seconds':round(time.monotonic()-started,2),
                'limitations':['Receipt classification can be resolved from its evidence while full filing and route coverage remains open.',
                 'Observed original giving is not a certification of complete unique dollars.',
                 'PAC outgoing amounts here are recipient-reported activity, not a complete Schedule B spending census.',
                 'Unresolved exposure can include overlapping representations and is not an error bound.',
                 'Automatically processed records do not establish the accuracy or completeness of the resulting totals.']}
        atomic(directory/'report.json',report)
        # The console's cross-reference tab reads this, not the report: clear
        # refunds, contradictions and unusually large counted rows, no totals.
        # Magnitude is informational only. The report workbook remains
        # the accounting of record and none of its keys move.
        atomic(directory/'digest.json',build_digest(
            result.get('decisions',[]),result.get('links',[]),changes,
            run_id=run_id,generated_at=report['generated_at'],
            entities={r['sub_id']:r.get('entity_type') for r in rows},
            source_urls={r['sub_id']:r.get('pdf_url') for r in rows},
            percentile=large_percentile,median_multiple=large_median_multiple))
        atomic(directory/'progress.json',{'schema_version':1,'run_id':run_id,'status':'completed','updated_at':now(),'steps':steps})
        atomic(state_root/'latest.json',{'schema_version':1,'run_id':run_id,'report_sha256':sha(directory/'report.json')})
        for name,items in [('changes',changes),('review',review),('coverage',coverage_tasks)]:
            with (directory/f'{name}.csv').open('w',newline='',encoding='utf-8-sig') as stream:
                if items:
                    writer=csv.DictWriter(stream,fieldnames=list(items[0]));writer.writeheader();writer.writerows(items)
        return report
    except Exception as error:
        for step in steps:
            if step['status']=='running':step.update(status='failed',detail='Processing stopped; resolve the saved evidence issue before continuing.')
        atomic(directory/'progress.json',{'schema_version':1,'run_id':run_id,'status':'failed','updated_at':now(),
                'steps':steps,'error':str(error)})
        raise
    finally:
        if store:store.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--industry',required=True)
    parser.add_argument('--cycles',default='2022,2024,2026')
    parser.add_argument('--state-root',type=Path)
    parser.add_argument('--baseline-root',type=Path)
    parser.add_argument('--evidence-dir',type=Path,action='append',default=[])
    parser.add_argument('--header-dir',type=Path,action='append',default=[])
    parser.add_argument('--company',action='append')
    parser.add_argument('--affiliations',type=Path)
    parser.add_argument('--filing-manifest',type=Path)
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--max-requests',type=int,default=100)
    args=parser.parse_args()
    if not 0<=args.max_requests<=10000:parser.error('--max-requests must be between 0 and 10000')
    def progress(value):
        step=next((s for s in value['steps'] if s['status']=='running'),value['steps'][-1])
        print(json.dumps({'run_id':value['run_id'],'step':step['id'],'detail':step['detail']}),flush=True)
    result=run(args.database,args.industry,[int(c) for c in args.cycles.replace('|',',').split(',')],args.state_root,
               args.baseline_root,args.evidence_dir,args.header_dir,not args.offline,args.max_requests,args.company,
               args.affiliations,args.filing_manifest,progress)
    print(json.dumps({'run_id':result['run_id'],'summary':result['summary'],'link_count':result['link_count'],
                      'unresolved_reasons':result['unresolved_reasons'],'excluded_reasons':result['excluded_reasons'],
                      'elapsed_seconds':result['elapsed_seconds']}),flush=True)


if __name__=='__main__':main()
