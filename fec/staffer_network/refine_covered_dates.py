"""Recover explicit written-out date ranges without inventing missing endpoints."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3

from .reconcile_held_evidence import interval, packed, sha

MONTHS={'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
MONTH=r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?'
ENDPOINT=rf'{MONTH}\s+(?:\d{{1,2}}(?:st|nd|rd|th)?\s*,?\s+)?(?:19|20)\d{{2}}'
RANGE=re.compile(rf'(?P<a>{ENDPOINT})\s*(?:[-–—]|\bto\b|\bthrough\b)\s*(?P<b>{ENDPOINT})',re.I)


def endpoint(value):
    match=re.fullmatch(rf'(?P<m>{MONTH})\s+(?:(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s+)?(?P<y>(?:19|20)\d{{2}})',value.strip(),re.I)
    if not match:return None
    return f"{match['y']}-{MONTHS[match['m'][:3].lower()]:02}-{int(match['d'] or 0):02}"


def assess(text,as_of):
    matches=list(RANGE.finditer(text))
    if not matches:return None
    # Another numeric range or another written-out interval may describe a different job.
    rest=RANGE.sub('',text)
    if len(matches)!=1 or re.search(r'\b\d{1,4}[/\d]*\s*[-–—]\s*\d',rest):
        return {'status':'MULTIPLE_INTERVALS_REVIEW','date_texts':[m[0] for m in matches]}
    m=matches[0];a,b=endpoint(m['a']),endpoint(m['b'])
    return {**interval(a,b,as_of),'raw_start':a,'raw_end':b,'source_date_text':m[0],
        'employer_title_pairing':'REQUIRES_ADJUDICATION','employment_promoted':False}


def run(ledger,output,as_of):
    ledger,output=map(Path,(ledger,output))
    if output.exists():raise ValueError('Choose a new immutable refinement output')
    input_hash=sha(ledger);counts=Counter();results=[]
    with sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True) as con:
        for ident,pid,status,payload in con.execute("SELECT id,person,status,payload FROM claim WHERE source='lda_covered_clause'"):
            counts['all_clauses_checked']+=1
            if status!='MISSING_DATE_BOUND':continue
            raw=json.loads(payload)['raw_text'];assessment=assess(raw,as_of)
            if assessment:
                results.append({'claim_id':ident,'source_person_id':pid,'source_text':raw,'assessment':assessment})
                counts[assessment['status']]+=1
    if sha(ledger)!=input_hash:raise ValueError('Input changed during refinement')
    result={'status':'PASS','as_of':as_of,'source_ledger':str(ledger),'source_ledger_sha256':input_hash,
        'builder_sha256':sha(Path(__file__)),'counts':dict(counts),'refinements':results,
        'production_activated':False,'missing_endpoints_filled':0,'two_digit_centuries_guessed':0}
    output.write_text(json.dumps(result,indent=2),encoding='utf8');print(packed({'status':'PASS','counts':dict(counts)}),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--ledger',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--as-of',default='2026-09-08')
    a=p.parse_args();run(a.ledger,a.output,a.as_of)
