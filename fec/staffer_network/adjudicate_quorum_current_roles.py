"""Resolve one-to-one Quorum identities from exact current Politico role evidence."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3

from .reconcile_held_evidence import digest, norm, office, packed, rows, sha, title
from .operational_spine import profile_current_roles, role_state


def exact_current_matches(quorum,politico):
    current,_=profile_current_roles(politico['payload'])
    qoffice=quorum['payload'].get('member_office_source_key','')
    qtitle=quorum['payload'].get('title','')
    return [role for role in current if office(role.get('employer'))==office(qoffice)
            and title(role.get('title'))==title(qtitle) and role_state(role.get('title'))=='SUBSTANTIVE']


def identity_reasons(qid,q_label,pid,p_label,names,reverse):
    key=norm(q_label);reasons=[]
    if key!=norm(p_label):reasons.append('QUORUM_POLITICO_NAME_NOT_EXACT')
    if len(key.split())<2:reasons.append('FULL_NAME_REQUIRED')
    if len(names[key])!=1:reasons.append('POLITICO_NAME_NOT_UNIQUE')
    if reverse[pid]!={qid}:reasons.append('REVERSE_CROSSWALK_NOT_UNIQUE')
    return reasons


def run(ledger,spine,crosswalk,output):
    ledger,spine,crosswalk,output=map(Path,(ledger,spine,crosswalk,output))
    if output.exists():raise ValueError('Choose a new immutable identity-supplement output')
    hashes={name:sha(path) for name,path in [('ledger',ledger),('spine',spine),('crosswalk',crosswalk)]}
    evidence=sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True)
    source=sqlite3.connect(spine.resolve().as_uri()+'?mode=ro',uri=True)
    people={r[0]:{'id':r[0],'source':r[1],'label':r[2],'payload':json.loads(r[3])}
            for r in source.execute("SELECT id,source,label,payload FROM source_records WHERE source IN ('quorum','politico')")}
    names=defaultdict(set)
    for ident,person in people.items():
        if person['source']=='politico':names[norm(person['label'])].add(ident)
    reverse=defaultdict(set)
    for row in rows(crosswalk):reverse[row['politico_id']].add(row['quorum_id'])
    counts=Counter();accepted=[];ambiguities=[];name_variants=[]
    decisions=evidence.execute("SELECT subject,proof FROM decision WHERE kind='quorum_politico' AND status='IDENTITY_REVIEW_REQUIRED' ORDER BY subject")
    for qid,raw in decisions:
        proof=json.loads(raw);candidates=proof.get('candidate_ids',[])
        if len(candidates)!=1:
            counts['MULTIPLE_POLITICO_CANDIDATES']+=1;continue
        pid=candidates[0];q,p=people.get(qid),people.get(pid)
        if not q or not p:
            counts['SOURCE_RECORD_MISSING']+=1;continue
        matches=exact_current_matches(q,p)
        if not matches:
            counts['CURRENT_ROLE_NOT_EXACT']+=1;continue
        full_name=norm(q['label']);gate_reasons=identity_reasons(qid,q['label'],pid,p['label'],names,reverse)
        record={'source':qid,'target':pid,'display_name':p['label'],
            'quorum_office':q['payload'].get('member_office_source_key',''),
            'quorum_title':q['payload'].get('title',''),'politico_current_roles':matches,
            'politico_same_name_ids':sorted(names[full_name]),'reverse_quorum_ids':sorted(reverse[pid]),
            'source_record_sha256':digest(q['payload']),'role_authority':'Politico',
            'quorum_service_dates_inferred':False,'payroll_used':False,'identity_scope':'source_binding'}
        if not gate_reasons:
            record['status']='ACCEPTED_EXACT_CURRENT_ROLE_SOURCE_BINDING';accepted.append(record);counts[record['status']]+=1
        elif 'QUORUM_POLITICO_NAME_NOT_EXACT' in gate_reasons:
            record['status']='NAME_VARIANT_REQUIRES_CORROBORATION';record['ambiguity_reasons']=gate_reasons
            name_variants.append(record);counts[record['status']]+=1
        else:
            record['status']='TARGETED_PAYROLL_OR_DATED_SOURCE_REVIEW';record['ambiguity_reasons']=gate_reasons
            ambiguities.append(record);counts[record['status']]+=1
    evidence.close();source.close()
    for name,path in [('ledger',ledger),('spine',spine),('crosswalk',crosswalk)]:
        if sha(path)!=hashes[name]:raise ValueError(name+' changed during adjudication')
    result={'status':'PASS','method':'UNIQUE_FULL_NAME_RECIPROCAL_CROSSWALK_EXACT_CURRENT_OFFICE_TITLE',
        'counts':dict(counts),'accepted':accepted,'targeted_payroll_ambiguities':ambiguities,'name_variant_review':name_variants,
        'inputs':{name:{'path':str(path),'sha256':hashes[name]} for name,path in [('ledger',ledger),('spine',spine),('crosswalk',crosswalk)]},
        'builder_sha256':sha(Path(__file__)),'production_activated':False,
        'policy':'Identity binding only. Politico supplies roles and dates. Quorum service dates are not inferred; ambiguous reverse mappings stay held.'}
    output.write_text(json.dumps(result,indent=2),encoding='utf8')
    print(packed({'status':'PASS','counts':dict(counts)}),flush=True);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger',type=Path,required=True);parser.add_argument('--spine',type=Path,required=True)
    parser.add_argument('--crosswalk',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.ledger,args.spine,args.crosswalk,args.output)
