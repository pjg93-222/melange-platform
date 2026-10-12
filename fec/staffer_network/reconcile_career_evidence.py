"""Reconcile all held career candidates without filling unknown career dates."""
from collections import Counter, defaultdict
import argparse
import json
from pathlib import Path
import re
import sqlite3

from fec.staffer_network.reconcile_held_evidence import (
    connect, digest, interval, norm, office, packed, rows, sha, title,
)
from fec.staffer_network.operational_spine import role_state
from fec.surfaces.staffer_identity_auto import compatible


def text_interval(text, as_of):
    """Extract a single explicit year or month/year interval, retaining precision."""
    text = str(text or '')
    months = list(re.finditer(r'(?<!\d)(\d{1,2})/((?:19|20)\d{2})\s*[-–—]\s*(\d{1,2})/((?:19|20)\d{2})(?!\d)',text))
    years = list(re.finditer(r'(?<![\d/])((?:19|20)\d{2})\s*[-–—]\s*((?:19|20)\d{2})(?!\d)',text))
    if len(months) + len(years) != 1:
        return {'status':'MULTIPLE_INTERVALS_REVIEW' if months or years else 'MISSING_DATE_BOUND'}
    if months:
        m=months[0]; a=f'{m[2]}-{int(m[1]):02}-00'; b=f'{m[4]}-{int(m[3]):02}-00'
    else:
        m=years[0];a=m[1]+'-00-00';b=m[2]+'-00-00'
    return {**interval(a,b,as_of),'raw_start':a,'raw_end':b,'matched_date_text':m[0]}


def contains_phrase(text, phrase):
    return bool(phrase) and ' '+phrase+' ' in ' '+text+' '


def run(directory, data, repo, as_of):
    directory, data, repo = map(Path,(directory,data,repo))
    progress=json.loads((directory/'progress.json').read_text())
    if progress.get('accounting_status')!='PASS':raise ValueError('Held source accounting must finish first')
    db=connect(directory/'evidence.sqlite')
    if db.execute("SELECT 1 FROM stage WHERE name='career_reconciliation'").fetchone():raise ValueError('Career reconciliation already exists')
    identities=json.loads((directory/'identity_input.json').read_bytes())
    aliases=identities.get('identity_aliases',{})
    def canonical(ident):
        seen=set()
        while ident in aliases and ident not in seen:
            seen.add(ident);ident=aliases[ident]
        return ident
    persons={r[0]:{'id':r[0],'source':r[1],'label':r[2],'canonical':canonical(r[3])} for r in db.execute('SELECT * FROM person')}
    orgs={r[0]:r[1] for r in db.execute('SELECT id,label FROM organization')}
    counts=Counter()
    # Import canonical organization display labels from the complete held catalog.
    catalog=data/'live_catalog/source_catalog_v1.sqlite'
    with sqlite3.connect(catalog.as_uri()+'?mode=ro',uri=True) as source:
        lsorgs=dict(source.execute("SELECT id,label FROM entities WHERE kind='organization'"))
    # Exact existing reporting periods are identity corroboration, never job dates.
    index=repo/'audits/staffer_live_runtime/lda_activity_v2.sqlite'
    firm_periods=defaultdict(lambda:defaultdict(list))
    with sqlite3.connect(index.resolve().as_uri()+'?mode=ro',uri=True) as source:
        for pid,firm,a,b in source.execute('SELECT DISTINCT person,firm_id,start,end_exclusive FROM member'):
            firm_periods[pid][norm(orgs.get(firm,''))].append((a,b,firm))
    candidatepath=repo/'audits/staffer_network/littlesis_lda_history_candidates_20260831/littlesis_lda_history_candidates.ndjson.gz'
    pair_support=defaultdict(list); all_pairs=set()
    for row in rows(candidatepath):
        lda,ls=row['lda_source_person_id'],row['littlesis_person_source_entity_id']
        all_pairs.add((lda,ls))
        if row['fact_type']!='EMPLOYMENT' or role_state(row.get('title_or_degree_raw'))!='SUBSTANTIVE':continue
        attrs=row.get('position_attributes') or {}
        if attrs.get('is_board') is True:continue
        a,b=persons.get('person:'+lda),persons.get(ls)
        if not a or not b or not compatible(a,b):continue
        dates=interval(row.get('raw_start_date'),row.get('raw_end_date'),as_of)
        if dates['status']!='BOUNDED_SOURCE_INTERVAL':continue
        firm_key=norm(row.get('container_display_name'))
        for start,end,firm in firm_periods.get(lda,{}).get(firm_key,[]):
            if max(start,dates['start']) < min(end,dates['end_exclusive']):
                pair_support[lda,ls].append({'source_relationship_id':row['source_relationship_id'],
                    'source_url':row['source_url'],'title':row.get('title_or_degree_raw'),'organization':row['container_display_name'],
                    'source_org_id':firm,'report_period':[start,end],'source_date_bounds':dates,
                    'raw_name_pair':[a['label'],b['label']],'employment_not_inferred_from_lda':True})
                break
    # Require reciprocal uniqueness after already accepted LDA aliases are applied.
    ls_to_canon=defaultdict(set);canon_to_ls=defaultdict(set)
    for (lda,ls),proof in pair_support.items():
        canon=canonical('person:'+lda);ls_to_canon[ls].add(canon);canon_to_ls[canon].add(ls)
    for lda,ls in sorted(all_pairs):
        canon=canonical('person:'+lda);proof=pair_support.get((lda,ls),[])
        status='ACCEPTED_SOURCE_BINDING' if proof and len(ls_to_canon[ls])==len(canon_to_ls[canon])==1 else 'IDENTITY_REVIEW_REQUIRED'
        db.execute('INSERT INTO decision VALUES(?,?,?,?,?,?)',(digest(['ls_lda',lda,ls]),'littlesis_lda',ls,canon,status,packed({'method':'COMPATIBLE_FULL_NAME_EXACT_EMPLOYER_AND_OVERLAPPING_DATED_POSITION','lda_source_id':lda,'support':proof,'candidate_input_sha256':sha(candidatepath) if counts['ls_pairs']==0 else None})))
        counts['ls_pairs']+=1;counts['ls_'+status]+=1
    print('LittleSis identity decisions',dict(counts),flush=True)
    # Covered-position text provides another independent office/title anchor for
    # exact full-name directory candidates; it does not supply absent dates.
    pol_by_name=defaultdict(list)
    for p in persons.values():
        if p['source']=='politico':pol_by_name[norm(p['label'])].append(p)
    pol_roles=defaultdict(list)
    for cid,pid,emp,job,a,b,payload in db.execute("SELECT id,person,employer,title,start,end_exclusive,payload FROM claim WHERE source='directory' AND status='BOUNDED_SOURCE_INTERVAL'"):
        pol_roles[pid].append((cid,emp,job,a,b,json.loads(payload)))
    covered_rows=list(db.execute("SELECT id,person,payload FROM claim WHERE source='lda_covered_position' AND status='SUBSTANTIVE_CLAIM_REQUIRES_RECONCILIATION'"))
    accepted_career_pairs=defaultdict(list)
    for cid,pid,payload in covered_rows:
        raw=json.loads(payload)['raw_text'];p=persons.get('person:'+pid)
        if not p:continue
        for ordinal,clause in enumerate(re.split(r'[;\n]+',raw)):
            if not norm(clause):continue
            dates=text_interval(clause,as_of)
            rs=role_state(clause)
            status='EXCLUDED_INTERN' if rs=='INTERN' else 'ROLE_REVIEW_REQUIRED' if rs=='MIXED_ROLE_REVIEW' else dates['status']
            claimid=digest([cid,ordinal,clause])
            db.execute('INSERT INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?)',(claimid,'lda_covered_clause',pid,None,None,dates.get('raw_start'),dates.get('raw_end'),dates.get('start'),dates.get('end_exclusive'),status,packed({'raw_text':clause,'parent_claim_id':cid,'date_assessment':dates,'employer_resolution':'REQUIRED'})))
            counts['covered_clauses']+=1;counts['covered_'+status]+=1
            if rs!='SUBSTANTIVE' or dates['status'] not in ('BOUNDED_SOURCE_INTERVAL','MISSING_DATE_BOUND'):continue
            text=norm(clause)
            for candidate in pol_by_name.get(norm(p['label']),[]):
                for roleid,employer,job,a,b,role in pol_roles[candidate['id']]:
                    source_role=role.get('assertion',role)
                    employer_label=source_role.get('employer') or role.get('employer')
                    if not employer_label:continue
                    member=office(employer_label)
                    if len(member.split())<2 or not contains_phrase(text,member) or not contains_phrase(text,title(job)):continue
                    if dates.get('start') and dates.get('end_exclusive') and max(a,dates['start'])>=min(b,dates['end_exclusive']):continue
                    accepted_career_pairs[pid,candidate['id']].append({'covered_claim_id':cid,'clause_id':claimid,'directory_role_id':roleid,'office':employer_label,'title':job,'directory_dates':[a,b],'covered_dates':dates,'dates_from_directory_only':dates['status']=='MISSING_DATE_BOUND'})
    lda_targets=defaultdict(set);pol_targets=defaultdict(set)
    for (lda,pol),proof in accepted_career_pairs.items():
        lda_targets[canonical('person:'+lda)].add(canonical(pol));pol_targets[canonical(pol)].add(canonical('person:'+lda))
    for (lda,pol),proof in sorted(accepted_career_pairs.items()):
        status='ACCEPTED_SOURCE_BINDING' if len(lda_targets[canonical('person:'+lda)])==len(pol_targets[canonical(pol)])==1 else 'IDENTITY_REVIEW_REQUIRED'
        db.execute('INSERT INTO decision VALUES(?,?,?,?,?,?)',(digest(['covered_pol',lda,pol]),'covered_politico','person:'+lda,canonical(pol),status,packed({'method':'EXACT_FULL_NAME_COVERED_OFFICE_TITLE_AND_DIRECTORY_HISTORY','support':proof,'source_ids':[lda,pol]})))
        counts['covered_identity_'+status]+=1
    # Every held LDA person gets an explicit career-coverage row, including those
    # without covered-position claims or any candidate public history.
    db.execute('''CREATE TABLE IF NOT EXISTS career_coverage(person TEXT PRIMARY KEY,label TEXT,
      covered_claims INTEGER, covered_dated_clauses INTEGER, ls_candidates INTEGER,
      accepted_identity_bindings INTEGER, status TEXT)''')
    covered_counts=dict(db.execute("SELECT person,count(*) FROM claim WHERE source='lda_covered_position' AND status='SUBSTANTIVE_CLAIM_REQUIRES_RECONCILIATION' GROUP BY person"))
    dated_counts=dict(db.execute("SELECT person,count(*) FROM claim WHERE source='lda_covered_clause' AND status='BOUNDED_SOURCE_INTERVAL' GROUP BY person"))
    candidates=Counter(lda for lda,ls in all_pairs)
    accepted=Counter()
    for lda,ls in pair_support:
        canon=canonical('person:'+lda)
        if len(ls_to_canon[ls])==len(canon_to_ls[canon])==1:accepted[lda]+=1
    for p in persons.values():
        if p['source']!='lda':continue
        pid=p['id'].removeprefix('person:');state='PARTIAL_HISTORY_RECONCILED' if accepted[pid] else 'PARTIAL_HISTORY_REVIEW' if covered_counts.get(pid) or candidates[pid] else 'FILING_ASSOCIATIONS_ONLY'
        db.execute('INSERT INTO career_coverage VALUES(?,?,?,?,?,?,?)',(pid,p['label'],covered_counts.get(pid,0),dated_counts.get(pid,0),candidates[pid],accepted[pid],state))
        counts['coverage_'+state]+=1
    receipt={'status':'PASS','counts':dict(counts),'lifetime_histories_certified':0,'policy':'No missing employment dates filled; internships excluded; all residual identities and employer mappings remain explicit review items.',
             'inputs':{'builder_sha256':sha(Path(__file__)),'history_candidates_sha256':sha(candidatepath)}}
    db.execute('INSERT INTO stage VALUES(?,?,?)',('career_reconciliation','PASS',packed(receipt)))
    db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.close()
    (directory/'career_reconciliation.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(packed(receipt),flush=True)
    return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--data',type=Path,default=Path.home()/'AppData/Local/Melange/staffer_network')
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2]);p.add_argument('--as-of',default='2026-09-08')
    a=p.parse_args();run(a.directory,a.data,a.repo,a.as_of)


if __name__=='__main__':main()
