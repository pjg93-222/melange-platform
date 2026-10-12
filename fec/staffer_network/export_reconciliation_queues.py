"""Account for every residual decision and undated claim, without silent truncation."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sqlite3

from .reconcile_held_evidence import sha, packed


def export(directory,candidate,refinements=None,date_scopes=None):
    directory,candidate=map(Path,(directory,candidate))
    release=json.loads(candidate.read_bytes());supplemented=bool(release.get('inputs',{}).get('identity_supplement'))
    out=directory/('review-queues-v4' if supplemented and date_scopes else 'review-queues-v3' if date_scopes else 'review-queues-v2' if refinements else 'review-queues-v1')
    if out.exists():raise ValueError('Queue export exists')
    out.mkdir();con=sqlite3.connect((directory/'evidence.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    manifest={'status':'PASS','scope':'All residual records in the bound held-evidence ledger, not a sample',
        'candidate_sha256':sha(candidate),'ledger_sha256':sha(directory/'evidence.sqlite'),'files':[]}
    refined={};scoped={};extra_dated=Counter()
    if refinements:
        refinements=Path(refinements);assessment=json.loads(refinements.read_bytes())
        if assessment['status']!='PASS' or assessment['source_ledger_sha256']!=manifest['ledger_sha256']:
            raise ValueError('Date refinements do not match the ledger')
        refined={r['claim_id']:r for r in assessment['refinements']}
        extra_dated.update(r['source_person_id'] for r in refined.values() if r['assessment']['status']=='BOUNDED_SOURCE_INTERVAL')
        manifest['date_refinements']={'path':str(refinements),'sha256':sha(refinements),'counts':assessment['counts']}
    if date_scopes:
        date_scopes=Path(date_scopes);scope_assessment=json.loads(date_scopes.read_bytes())
        if not refinements or scope_assessment.get('status')!='PASS' or scope_assessment.get('source_refinements_sha256')!=sha(refinements):
            raise ValueError('Date-scope adjudications do not match the refinements')
        scoped={r['claim_id']:r for r in scope_assessment['adjudications']}
        if set(scoped)!={i for i,r in refined.items() if r['assessment']['status']=='BOUNDED_SOURCE_INTERVAL'}:
            raise ValueError('Date-scope adjudication accounting mismatch')
        manifest['date_scope_adjudications']={'path':str(date_scopes),'sha256':sha(date_scopes),'counts':scope_assessment['counts'],
            'policy':scope_assessment['policy']}
    def write(name,records):
        path=out/(name+'.ndjson.gz');count=0;statuses=Counter()
        with gzip.open(path,'wt',encoding='utf8') as stream:
            for record in records:
                stream.write(packed(record)+'\n');count+=1;statuses[record.get('status','REVIEW')]+=1
        manifest['files'].append({'role':name,'path':str(path.resolve()),'records':count,'statuses':dict(statuses),'sha256':sha(path)})
    def identity_reviews():
        released={b['source'] for b in release['identity_bindings']}
        for ident,kind,subject,target,status,proof in con.execute("SELECT * FROM decision WHERE status IN ('IDENTITY_REVIEW_REQUIRED','CLIENT_IDENTITY_REVIEW_REQUIRED','NO_RETRIEVED_CANDIDATE') ORDER BY kind,subject"):
            if subject in released:continue
            yield {'id':ident,'kind':kind,'subject':subject,'target':target,'status':status,'evidence':json.loads(proof),
                'payroll_policy':'Targeted corroboration only after the conflicting identity/office/date question is specified; never a broad population source.'}
        for record in release['held_support']:
            yield {**record,'status':'RELEASE_SUPPORT_WITHHELD','whole_binding_held':record['whole_binding_held']}
    write('identity_and_organization_review',identity_reviews())
    def career_reviews():
        for row in con.execute("SELECT id,source,person,employer,title,status,payload FROM claim WHERE status NOT IN ('BOUNDED_SOURCE_INTERVAL','EXCLUDED_INTERN','EXPLICIT_NO_POSITION','EMPTY_OR_PUNCTUATION') ORDER BY source,person,id"):
            ident,source,person,employer,title,status,payload=row
            evidence=json.loads(payload)
            if ident in refined:
                evidence['date_refinement']=refined[ident]['assessment']
                status=evidence['date_refinement']['status']
                if status=='BOUNDED_SOURCE_INTERVAL':
                    if ident in scoped:
                        evidence['date_scope_adjudication']=scoped[ident]
                        status=scoped[ident]['status']
                    else:status='DATED_INTERVAL_REQUIRES_EMPLOYER_ADJUDICATION'
            yield {'claim_id':ident,'source':source,'person':person,'employer':employer,'title':title,'status':status,'evidence':evidence}
    write('career_claim_review',career_reviews())
    accepted=Counter(b['source'].removeprefix('person:') for b in release['identity_bindings'] if b['kind']=='covered_politico')
    history=json.loads((directory/'identity_input.json').read_bytes());aliases=history.get('identity_aliases',{})
    def canonical(i):
        seen=set()
        while i in aliases:
            if i in seen:raise ValueError('Source identity cycle')
            seen.add(i);i=aliases[i]
        return i
    def coverage():
        for pid,label,claims,dated,ls,old_accepted,old_status in con.execute('SELECT * FROM career_coverage ORDER BY person'):
            public=release['person_evidence'].get(canonical('person:'+pid),[])
            state='PARTIAL_HISTORY_RECONCILED' if accepted[pid] or public else old_status
            yield {'source_person_id':pid,'label':label,'covered_claims':claims,'covered_dated_clauses':dated+extra_dated[pid],
                'additional_dated_clauses_from_written_month_ranges':extra_dated[pid],
                'littlesis_candidate_pairs':ls,'accepted_covered_directory_bindings':accepted[pid],
                'official_career_and_affiliation_observations':len(public),'status':state,
                'complete_lifetime_history':False,'no_network_inference_from_missing_data':True}
    write('lda_career_coverage',coverage())
    manifest['claim_accounting']=[dict(zip(('source','status','records'),r)) for r in con.execute('SELECT source,status,count(*) FROM claim GROUP BY source,status')]
    manifest['decision_accounting']=[dict(zip(('kind','status','records'),r)) for r in con.execute('SELECT kind,status,count(*) FROM decision GROUP BY kind,status')]
    manifest['remaining_work']=['Resolve source identity and organization review cases with additional corroboration.',
        'Parse remaining dated career text and resolve office/title associations; retain unknown endpoints.',
        'Adjudicate newly collected historical covered-position claims and integrate verified historical windows.',
        'Activate and evaluate the separately built operational spine and work-proximity-by-time model in its own release.']
    con.close();(out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print(packed({'status':'PASS','files':[{k:f[k] for k in ('role','records')} for f in manifest['files']]}),flush=True)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--refinements',type=Path);p.add_argument('--date-scopes',type=Path)
    args=p.parse_args();export(args.directory,args.candidate,args.refinements,args.date_scopes)
