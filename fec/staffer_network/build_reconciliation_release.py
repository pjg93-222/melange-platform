"""Build a reviewable, immutable overlay from the fully accounted evidence ledger.

This never promotes LDA observations to employment, guesses career end dates,
or adds anyone to the operational staffer population.
"""
import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import sqlite3

from .reconcile_held_evidence import norm, packed, sha, digest, title, office
from fec.surfaces.staffer_identity_auto import compatible


def covered_support_check(raw, proof):
    """Reject mixed offices, unparsed dates, and a shorter title inside another title."""
    text=norm(raw); reasons=[]
    offices=re.findall(r'\b(?:rep\.?|representative|sen\.?|senator|congressman|congresswoman)\s+',raw,re.I)
    if len(offices)>1 or len(re.findall(r'\bcommittee\b',raw,re.I))>1:
        reasons.append('MULTIPLE_OFFICES_IN_CLAUSE')
    if proof['covered_dates']['status']=='MISSING_DATE_BOUND' and re.search(r'\b(?:19|20)\d{2}\b|\b\d{1,2}/\d{2}\b',raw):
        reasons.append('EXPLICIT_DATE_TEXT_REQUIRES_PARSING')
    employer=proof['office']
    if re.match(r'\s*Sen\.?\s',employer,re.I) and re.search(r'\b(?:Rep\.?|Representative|Congressman|Congresswoman)\s',raw,re.I):
        reasons.append('HISTORICAL_CHAMBER_LABEL_CONFLICT')
    if re.match(r'\s*Rep\.?\s',employer,re.I) and re.search(r'\b(?:Sen\.?|Senator)\s',raw,re.I):
        reasons.append('HISTORICAL_CHAMBER_LABEL_CONFLICT')
    job=title(proof['title']); normalized_title_text=title(raw)
    exact=[]
    for match in re.finditer(r'(?<!\w)'+re.escape(job)+r'(?!\w)',normalized_title_text):
        prefix=normalized_title_text[:match.start()].split()
        if not prefix or prefix[-1] not in {'senior','deputy','acting','assistant','associate','environmental'}:
            exact.append(match)
    if not exact:reasons.append('TITLE_IS_SUBSTRING_OF_DIFFERENT_ROLE')
    return reasons


def run(directory, repo, output, identity_supplement=None):
    directory,repo,output=map(Path,(directory,repo,output))
    if output.exists():raise ValueError('Release paths are immutable; choose a new path')
    progress=json.loads((directory/'progress.json').read_bytes())
    if progress.get('accounting_status')!='PASS':raise ValueError('Source accounting incomplete')
    db=sqlite3.connect((directory/'evidence.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    identities=json.loads((directory/'identity_input.json').read_bytes())
    aliases=identities.get('identity_aliases',{})
    def canonical(ident):
        seen=set()
        while ident in aliases:
            if ident in seen:raise ValueError('Cycle in held identity evidence')
            seen.add(ident);ident=aliases[ident]
        return ident
    sources={r['name']:r for r in json.loads((repo/'audits/exhaustive_reconciliation_20260908/official_sources/manifest.json').read_bytes()) if r['status']=='FETCHED'}
    for source in sources.values():
        if sha(source['path'])!=source['sha256']:raise ValueError('Official source hash mismatch')
    def citation(key,page=None):
        result={k:sources[key][k] for k in ('url','path','sha256')}
        result['capture_kind']=sources[key].get('capture_kind','ORIGINAL_SOURCE_BYTES')
        if page:result['page']=page
        return result
    persons={r[0]:{'id':r[0],'source':r[1],'label':r[2],'canonical':canonical(r[3])} for r in db.execute('SELECT * FROM person')}
    organizations={r[0]:{'id':r[0],'label':r[1],'kind':r[2],'record':json.loads(r[3])} for r in db.execute('SELECT * FROM organization')}
    bindings=[];held=[];counts=Counter()
    for ident,kind,subject,target,status,proof in db.execute("SELECT * FROM decision WHERE status='ACCEPTED_SOURCE_BINDING' ORDER BY kind,subject"):
        proof=json.loads(proof)
        if kind=='covered_politico':
            accepted=[];rejected=[]
            for support in proof['support']:
                raw=json.loads(db.execute('SELECT payload FROM claim WHERE id=?',(support['clause_id'],)).fetchone()[0])['raw_text']
                reasons=covered_support_check(raw,support)
                if reasons:rejected.append({**support,'raw_text':raw,'reasons':reasons})
                else:accepted.append({**support,'raw_text':raw})
            if rejected:held.append({'source':subject,'target':target,'kind':kind,'rejected_support':rejected,'whole_binding_held':not accepted})
            if not accepted:counts['covered_bindings_withheld']+=1;continue
            proof['support']=accepted
        bindings.append({'source':subject,'target':canonical(target),'kind':kind,'decision_id':ident,'evidence':proof})
        counts['bindings_'+kind]+=1
    supplement=None
    if identity_supplement:
        identity_supplement=Path(identity_supplement);supplement=json.loads(identity_supplement.read_bytes())
        if supplement.get('status')!='PASS' or supplement.get('inputs',{}).get('ledger',{}).get('sha256')!=sha(directory/'evidence.sqlite'):
            raise ValueError('Identity supplement does not match the evidence ledger')
        supplement_hash=sha(identity_supplement)
        for row in supplement['accepted']:
            bindings.append({'source':row['source'],'target':canonical(row['target']),'kind':'quorum_politico_current',
                'decision_id':'supplement:'+digest([row['source'],row['target'],supplement_hash]),
                'evidence':{**row,'method':supplement['method'],'source_ids':[row['source'],row['target']],
                    'supplement_path':str(identity_supplement),'supplement_sha256':supplement_hash}})
            counts['bindings_quorum_politico_current']+=1
    client_groups={}
    for subject,target,proof in db.execute("SELECT subject,target,proof FROM decision WHERE status='ACCEPTED_CORROBORATED_CLIENT_BINDING'"):
        proof=json.loads(proof)
        client_groups.setdefault(target,{'primary_id':target,'source_ids':proof['peer_ids'],'method':proof['method'],
            'evidence':{'source_records':[organizations[i] for i in proof['peer_ids']],
                'legal_parent_subsidiary_merge':False,'employment_asserted':False}})
    # Individually inspected name variants, not a global suffix/DBA transformation.
    manual=[('lda:client:103080',[100935,103080,114550,151321,184933,56002,60468,61170,72644],
             'American Council of Life Insurers',citation('acli_2018')),
            ('lda:client:115381',[110165,115381,192149,56184],
             "Credit Union National Association, Inc. DBA America's Credit Unions",None)]
    for primary,numbers,label,source in manual:
        ids=['lda:client:'+str(n) for n in numbers]
        records=[organizations[i] for i in ids]
        if any(r['record']['state_variants']!=['DC'] or r['record']['country_variants']!=['US'] for r in records):
            raise ValueError('Curated organization jurisdiction changed')
        # Remove only groups fully included in this reviewed set; reject partial overlap.
        for key,g in list(client_groups.items()):
            overlap=set(g['source_ids']) & set(ids)
            if overlap:
                if not set(g['source_ids'])<=set(ids):raise ValueError('Manual organization group conflicts')
                del client_groups[key]
        client_groups[primary]={'primary_id':primary,'label':label,'source_ids':ids,'method':'INDIVIDUALLY_REVIEWED_SOURCE_NAME_VARIANTS_AND_JURISDICTION',
            'evidence':{'source_records':records,'official_reference':source,
            'reason':'Full organization names, matching DC/US jurisdiction and compatible trade-association descriptions; exact listed variants reviewed together.',
            'inference':True,'legal_parent_subsidiary_merge':False,'predecessor_successor_merge':False,'employment_asserted':False}}
    nodes={};person_evidence=defaultdict(list);official=[];roster_decisions=[]
    index=sqlite3.connect((repo/'audits/staffer_live_runtime/lda_activity_v2.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    context=defaultdict(set)
    for pid,firm in index.execute('SELECT DISTINCT person,firm_id FROM member'):
        context['person:'+pid].add(norm(organizations.get(firm,{}).get('label')))
    index.close()
    def roster_person(name,employer,role,source,date_description):
        # Same professional full name AND independently held organizational anchor.
        reviewed_names={'Doug Lathrop':('Douglas Lathrop','doug_bio_verified_text'),
                        'Ally Perleoni':('Allyson Perleoni','perleoni_wgr_verified_text')}
        search_name=reviewed_names.get(name,(name,None))[0]
        candidates=[p for p in persons.values() if p['source']=='lda' and compatible(p,{'label':search_name})
                    and norm(employer) in context[p['id']]]
        roots={p['canonical'] for p in candidates}
        ident=next(iter(roots)) if len(roots)==1 else 'person:official:'+digest([name,employer])[:24]
        roster_decisions.append({'name':name,'organization':employer,'node_id':ident,'source_candidates':[p['id'] for p in candidates],
            'status':'COMPATIBLE_FULL_NAME_AND_HELD_ORGANIZATION_ANCHOR' if len(roots)==1 else 'SEPARATE_SOURCE_IDENTITY_REVIEW', 'source':source,
            'individually_reviewed_name_variant_evidence':citation(reviewed_names[name][1]) if name in reviewed_names else None})
        if not ident.startswith('person:official:'):
            label=persons.get(ident,{}).get('label',name)
        else:label=name
        nodes.setdefault(ident,{'id':ident,'kind':'person','label':label,'name_status':'SOURCE_REPORTED',
            'source_summary':'Public professional roster evidence; career dates may be incomplete.',
            'provenance':{'method':'OFFICIAL_PROFESSIONAL_ROSTER','citation':source},'source_person_ids':[ident],
            'operational_staffer_qualification':False,'ecosystem_only':True})
        person_evidence[ident].append({'title':role,'organization':employer,'date_description':date_description,
            'status':'SOURCE_OBSERVATION','start':None,'end':None,'continuous_employment_asserted':False,'source':source})
        return ident
    acli='American Council of Life Insurers'
    acli_roles=[('Kathleen Coulombe','Senior Vice President, Federal Relations'),('Lauren Colicelli','Director, Strategic Political Engagement'),
        ('Whitney Dunn','Senior Director, Strategic Initiatives and Government Relations'),('Lauryl Jackson','Vice President, Financial Income Security and Diversity & Inclusion'),
        ('LaVette Johnson','Executive Assistant'),('Paul Kangas','Vice President, Financial Services and Principal Deputy'),
        ('Jill Kozeny','Executive Vice President and Chief Advocacy Officer'),('Ian Steger','Legislative Director'),
        ('Amanda Smith','Legislative Analyst'),('Doug Lathrop','Vice President, Taxation')]
    acli_ids=[roster_person(n,acli,r,citation('acli_2025_pdf',23),'September 2025 team roster; exact service dates not supplied') for n,r in acli_roles]
    official.append({'id':'official:acli:team:2025-09','member_ids':acli_ids,'relationship':'official_team_roster',
        'label':'Listed on the same official team roster','organization_id':'organization:official:acli','organization_label':acli,
        'date_description':'September 2025 roster observation; continuous employment duration is not established',
        'cost':3,'provenance':citation('acli_2025_pdf',23)})
    # This is an announced board term. It is not a statement of uninterrupted service.
    board=[('Chatrane Birbal','HR Policy Association','President'),('Liz Westbrook','Women in Government Relations','President-Elect'),
        ('Apiyo Oloya','Department of Justice','Treasurer'),('Alpha Lillstrom Cheng','Lillstrom Cheng Strategies','Secretary'),
        ('Kathleen Coulombe',acli,'Past President'),('Elizabeth Best-Bradford','American Speech-Language-Hearing Association','Board member'),
        ('Tara Brown','Department of Energy','Board member'),('Courtney Hogan Nese','PepsiCo','Board member'),
        ('Ally Perleoni','Association of American Medical Colleges','Board member'),
        ('Jeannine Raymond','National Association of State Retirement Administrators','Board member')]
    board_ids=[roster_person(n,e,r+' of Women in Government Relations',citation('wgr_2024_pdf',1),
        'Board term announced May 8, 2024, starting July 1, 2024, for 2024–2026; actual end date unverified') for k,(n,e,r) in enumerate(board)]
    # Board roles are affiliations with WGR, not jobs at the member's separately listed employer.
    for ident in board_ids:
        person_evidence[ident][-1]['organization']='Women in Government Relations'
        person_evidence[ident][-1]['status']='ANNOUNCED_BOARD_AFFILIATION'
    official.append({'id':'official:wgr:board:2024','member_ids':board_ids,'relationship':'announced_board_co_membership',
        'label':'Named to the same announced board term','organization_id':'organization:official:wgr','organization_label':'Women in Government Relations',
        'date_description':'2024–2026 term announced May 8, 2024, beginning July 1; actual continuous tenure unverified',
        'cost':4,'provenance':citation('wgr_2024_pdf')})
    kathleen=acli_ids[0]
    dent=roster_person('Alane Dent',acli,'Acting Senior Vice President, Federal Relations',citation('acli_2018'),
        'January 23, 2018 announcement; exact job start and end dates not supplied')
    official.append({'id':'official:acli:reporting-line:2018','member_ids':[kathleen,dent],
        'relationship':'official_reporting_line','label':'Officially reported reporting relationship',
        'organization_id':'organization:official:acli','organization_label':acli,
        'date_description':'January 23, 2018 announcement: Kathleen Coulombe reports to Alane Dent; duration unverified',
        'cost':2,'provenance':citation('acli_2018')})
    for org,role,date_description,key,extra in [
        ('Office of Nevada Governor Kenny Guinn','Deputy Director of Constituent Services / Las Vegas office assistant','Dates not supplied','acli_bio',{}),
        ('Department of Defense, Directorate of Defense Systems','Staff role; exact title not supplied','Joined in 2004; exact start and end unknown','acli_bio',{'start_year':2004}),
        ('Society for Human Resource Management','Senior Advisor, Government Relations','Joined the organization in May 2005; start of this particular role and exact end not supplied','acli_bio',{'organization_join_month':'2005-05'}),
        (acli,'Vice President, Federal Relations (retirement security)','Appointment announced January 23, 2018; exact start not supplied','acli_2018',{'announcement_date':'2018-01-23'}),
        (acli,'Senior Vice President, Federal Relations','Promotion announced September 7, 2023; exact end not supplied','acli_2023',{'announcement_date':'2023-09-07'}),
        ("America's Credit Unions",'Chief Advocacy Officer','Started April 20, 2026; subsequent end date not supplied','acu_start_verified_text',{'explicit_start_date':'2026-04-20'}),
    ]:
        person_evidence[kathleen].append({'organization':org,'title':role,'date_description':date_description,
            'status':'PARTIALLY_DATED_CAREER_CLAIM','start':None,'end':None,**extra,'source':citation(key)})
    groups=list(client_groups.values())
    membership=[i for g in groups for i in g['source_ids']]
    if len(membership)!=len(set(membership)):raise ValueError('Conflicting client groups')
    if len({b['source'] for b in bindings})!=len(bindings):raise ValueError('Conflicting identity bindings')
    counts.update(client_groups=len(groups),client_source_records=len(membership),official_associations=len(official),
                  official_roster_person_ids=len(nodes),official_source_only_person_ids=sum(i.startswith('person:official:') for i in nodes),
                  career_claims=sum(map(len,person_evidence.values())))
    receipt={'schema':'held-evidence-reconciliation-release-v1','status':'CANDIDATE','as_of':'2026-09-08',
        'identity_bindings':bindings,'client_groups':groups,'official_associations':official,'nodes':list(nodes.values()),
        'person_evidence':dict(person_evidence),'roster_identity_decisions':roster_decisions,'held_support':held,
        'coverage':{'counts':dict(counts),'lifetime_histories_certified':0,'payroll_additional_collection':False,
            'historical_backfill_complete':False,'operational_spine_activated':False},
        'inputs':{'ledger':str(directory/'evidence.sqlite'),'ledger_sha256':sha(directory/'evidence.sqlite'),
            'builder_sha256':sha(Path(__file__)),'source_accounting':str(directory/'progress.json'),
            **({'identity_supplement':str(identity_supplement),'identity_supplement_sha256':sha(identity_supplement)} if supplement else {})},
        'policy':'Reconciled identity bindings and explicitly labeled ecosystem associations only. Source observations never establish employment duration. Unknown dates and conflicting histories remain in review.'}
    output.parent.mkdir(parents=True,exist_ok=True);tmp=output.with_name(output.name+'.tmp');tmp.write_text(json.dumps(receipt,indent=2),encoding='utf8');os.replace(tmp,output)
    db.close();print(packed({'path':str(output),'status':receipt['status'],'counts':dict(counts)}),flush=True)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2]);parser.add_argument('--identity-supplement',type=Path);args=parser.parse_args()
    run(args.directory,args.repo,args.output,args.identity_supplement)
