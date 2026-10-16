"""Validate candidate identity overlays and exercise real ecosystem routes offline."""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sqlite3
import time

from .reconcile_held_evidence import sha
from fec.surfaces.staffer_reconciled_evidence import merge_bindings, bind_client_groups, augment_inventory
from fec.surfaces.staffer_person_inventory import apply_identity_overlay
from fec.surfaces.staffer_activity_index import ActivityIndex
from fec.surfaces.staffer_routing import AdvocacyRouter


def run(candidate,repo,output):
    candidate,repo,output=map(Path,(candidate,repo,output))
    if output.exists():raise ValueError('Evaluation output already exists')
    release=json.loads(candidate.read_bytes())
    if release['status']!='CANDIDATE':raise ValueError('Expected a candidate release')
    ledger=Path(release['inputs']['ledger'])
    if sha(ledger)!=release['inputs']['ledger_sha256']:raise ValueError('Candidate ledger has changed')
    with sqlite3.connect(ledger.as_uri()+'?mode=ro',uri=True) as con:
        if con.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Ledger integrity failure')
        nodes={pid:{'id':pid,'kind':'person','label':label} for pid,source,label in con.execute('SELECT id,source,label FROM person') if source!='littlesis'}
        for ident,label in con.execute('SELECT id,label FROM organization'):
            nodes['organization:'+ident]={'id':'organization:'+ident,'kind':'organization','label':label}
        supplement=None
        if release['inputs'].get('identity_supplement'):
            path=Path(release['inputs']['identity_supplement'])
            if sha(path)!=release['inputs']['identity_supplement_sha256']:raise ValueError('Identity supplement hash mismatch')
            supplement=json.loads(path.read_bytes())
            if supplement.get('status')!='PASS' or supplement.get('inputs',{}).get('ledger',{}).get('sha256')!=release['inputs']['ledger_sha256']:
                raise ValueError('Identity supplement does not match the ledger')
        supplement_bindings={(r['source'],r['target']) for r in (supplement or {}).get('accepted',[])}
        for binding in release['identity_bindings']:
            if binding['kind']=='quorum_politico_current':
                pair=(binding['source'],binding['evidence']['source_ids'][1])
                if pair not in supplement_bindings:raise ValueError('Binding is not in the verified identity supplement')
            else:
                row=con.execute('SELECT subject,target,status FROM decision WHERE id=?',(binding['decision_id'],)).fetchone()
                if not row or row[0]!=binding['source'] or row[2]!='ACCEPTED_SOURCE_BINDING':raise ValueError('Binding is not an accepted source decision')
        source_counts=dict(con.execute('SELECT source,count(*) FROM claim GROUP BY source'))
    held=json.loads((ledger.parent/'identity_input.json').read_bytes())
    user_assertions=json.loads((repo/'fec/surfaces/staffer_user_assertions.json').read_bytes())
    held['identity_groups']=[*user_assertions.get('identity_groups',[]),*held['identity_groups']]
    for group in held['identity_groups']:
        nodes.setdefault(group['primary_id'],{'id':group['primary_id'],'kind':'person','label':group['display_name']})
    original={'nodes':{},'sources':[]};apply_identity_overlay(original,nodes,held['identity_groups'])
    nodes.update(original['nodes'])
    for alias in original['identity_aliases']:nodes.pop(alias,None)
    database=repo/'audits/staffer_live_runtime/lda_activity_v2.sqlite'
    before=ActivityIndex(database,nodes,reconciliation_release=None)
    release={**release,'release_path':str(candidate),'release_sha256':sha(candidate)}
    added={'nodes':{},'sources':[]};augment_inventory(added,release,nodes)
    after_nodes={**nodes,**added['nodes']}
    separated=user_assertions.get('keep_separate_pairs',[])
    decisions=merge_bindings({'identity_groups':[],'identity_aliases':{},'unresolved_candidates':[]},after_nodes,
        original['identity_aliases'],held['identity_groups'],release,separated)
    combined={'nodes':{},'sources':[]}
    apply_identity_overlay(combined,after_nodes,[*held['identity_groups'],*decisions['identity_groups']])
    after_nodes.update(combined['nodes'])
    for alias in combined['identity_aliases']:after_nodes.pop(alias,None)
    after=ActivityIndex(database,after_nodes,reconciliation_release=None)
    bind_client_groups(after,release);after.reconciliation_as_of=release['as_of']
    aliases=combined['identity_aliases']
    def canonical(ident):
        seen=set()
        while ident in aliases:
            if ident in seen:raise ValueError('Resulting identity cycle')
            seen.add(ident);ident=aliases[ident]
        return ident
    after.official_associations=[{**g,'member_ids':sorted({canonical(i) for i in g['member_ids']})} for g in release['official_associations']]
    options=AdvocacyRouter._options({'include_review':True,'include_policy':False,'max_neighbor_checks':2000000,'timeout_ms':10000})
    def neighbors(index,ident):
        return index.neighbors(ident,options,{'neighbor_checks':0,'deadline':time.monotonic()+30})
    k=canonical('person:lda:lobbyist:154321')
    sample=[k]+random.Random(20260908).sample(sorted(before.to_source),40)
    sample=list(dict.fromkeys(sample+[canonical(i) for g in release['official_associations'] for i in g['member_ids']]))
    rows=[];classes=Counter();began=time.monotonic()
    for ident in sample:
        target=canonical(ident);a=neighbors(before,ident);b=neighbors(after,target)
        for other,edge in b.items():
            if edge['continuous_employment_asserted'] is not False:raise ValueError(f'continuous_employment_asserted failed for {target}/{other}')
            if edge['personal_familiarity_established'] is not False:raise ValueError(f'personal_familiarity_established failed for {target}/{other}')
            if edge.get('observed_periods'):
                if len({(p['start'],p['end_exclusive']) for p in edge['observed_periods']})!=edge['observed_period_count']:raise ValueError(f'observed_period_count failed for {target}/{other}')
            if edge['relationship']=='shared_client_period' and not edge['via_organization']:raise ValueError(f'shared_client_period via_organization failed for {target}/{other}')
            classes[edge['relationship']]+=1
        for other in sorted(b)[:3]:
            reverse=neighbors(after,other).get(target)
            if reverse is None:raise ValueError(f'Reverse edge missing for {target}/{other}')
            if (reverse['cost'],reverse['relationship'],reverse.get('observed_period_count'))!=(b[other]['cost'],b[other]['relationship'],b[other].get('observed_period_count')):raise ValueError(f'reverse-edge symmetry failed for {target}/{other}')
        rows.append({'source_id':ident,'canonical_id':target,'label':after_nodes[target]['label'],'before':len(a),'after':len(b)})
        print(json.dumps(rows[-1]),flush=True)
    k_neighbors=neighbors(after,k)
    report={'status':'PASS','candidate_sha256':sha(candidate),'ledger_sha256':sha(ledger),'source_claim_counts':source_counts,
        'source_accounting':json.loads((ledger.parent/'progress.json').read_bytes())['accounting_status'],
        'sample_basis':'Deterministic 40 LDA identities, Kathleen, and every curated roster identity. Not a manual precision certification.',
        'identity_bindings':decisions['reconciled_evidence'],'client_version_precedence':after.reconciled_filing_counts,
        'sample_size':len(sample),'sample':rows,'relationship_counts':dict(classes),
        'kathleen_connections':[{'id':i,'label':after_nodes[i]['label'],'relationship':e['relationship'],'periods':e.get('observed_period_count'),'via':e.get('via_organization')} for i,e in k_neighbors.items()],
        'seconds':time.monotonic()-began,'operational_staffer_population_changed':False,'work_duration_inferred_from_lda_or_rosters':False,
        'historical_backfill_complete':False,'remaining_identity_conflicts':decisions['reconciled_evidence']['held']}
    output.write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'status':'PASS','sample_size':len(sample),'kathleen_connections':len(k_neighbors),
        'new_bindings':decisions['reconciled_evidence']['new_bindings'],'client_version_precedence':after.reconciled_filing_counts}),flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2]);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.candidate,a.repo,a.output)
