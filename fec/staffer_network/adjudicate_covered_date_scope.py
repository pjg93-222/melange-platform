"""Bind explicit date ranges to their source clause without inventing structured jobs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from .reconcile_held_evidence import packed, sha


def career_context(source_text, date_text):
    """Return the preserved non-date clause and its structural review state."""
    text=source_text.replace(date_text,' ',1)
    text=re.sub(r'\s+',' ',text).strip(' \t\r\n.,:;-–—()[]')
    if not re.search(r'[A-Za-z]',text):
        return text,'NO_CAREER_CONTEXT'
    if re.match(r'(?i)^(?:and|to)\b',text):
        return text,'CONTEXT_DEPENDENT_FRAGMENT'
    return text,'SINGLE_CLAUSE_DATE_SCOPE_CONFIRMED'


def run(refinements,output):
    refinements,output=map(Path,(refinements,output))
    if output.exists():raise ValueError('Choose a new immutable date-scope output')
    input_hash=sha(refinements);source=json.loads(refinements.read_bytes())
    if source.get('status')!='PASS':raise ValueError('Date refinements are not PASS')
    counts=Counter();results=[]
    for row in source.get('refinements',[]):
        assessment=row['assessment']
        if assessment.get('status')!='BOUNDED_SOURCE_INTERVAL':continue
        context,status=career_context(row['source_text'],assessment['source_date_text'])
        counts[status]+=1
        results.append({'claim_id':row['claim_id'],'source_person_id':row['source_person_id'],
            'status':status,'career_context':context,'source_date_text':assessment['source_date_text'],
            'date_scope':'SOURCE_CLAUSE_ONLY','structured_title_status':'UNRESOLVED',
            'structured_employer_status':'UNRESOLVED','employment_promoted':False})
    if sha(refinements)!=input_hash:raise ValueError('Input changed during date-scope adjudication')
    if sum(counts.values())!=source.get('counts',{}).get('BOUNDED_SOURCE_INTERVAL'):
        raise ValueError('Bounded refinement accounting mismatch')
    result={'status':'PASS','source_refinements':str(refinements),'source_refinements_sha256':input_hash,
        'source_ledger_sha256':source['source_ledger_sha256'],'builder_sha256':sha(Path(__file__)),
        'counts':dict(counts),'adjudications':results,'production_activated':False,
        'policy':'Dates apply only to the preserved source clause. Structured title, employer and identity remain unresolved.'}
    output.write_text(json.dumps(result,indent=2),encoding='utf8')
    print(packed({'status':'PASS','counts':dict(counts)}),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refinements',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.refinements,args.output)
