"""Hash-bound, reversible source reconciliation overlays for the live view."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3


def load_release(runtime):
    pin=Path(runtime)/'reconciliation_pin.json'
    if not pin.exists():return None
    spec=json.loads(pin.read_text(encoding='utf-8-sig'))
    if spec.get('status')!='PASS':raise ValueError('Reconciliation pin is not PASS')
    path=Path(spec['path'])
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=spec['sha256']:raise ValueError('Reconciliation release hash mismatch')
    release=json.loads(raw)
    if release.get('status')!='PASS':raise ValueError('Reconciliation release not verified')
    return {**release,'release_path':str(path),'release_sha256':spec['sha256']}


def augment_inventory(result, release, existing=None):
    if not release:return
    existing=existing or {}
    spine_links=defaultdict(set)
    for ident,node in existing.items():
        if node.get('lda_source_person_id') and (node.get('spine_person_id') or ident.startswith('person:hill:person:')):
            spine_links['person:'+node['lda_source_person_id']].add(ident)
    def target(ident):
        linked=spine_links.get(ident,set())
        return next(iter(linked)) if len(linked)==1 else ident
    for node in release.get('nodes',[]):
        ident=target(node['id'])
        if ident not in result['nodes']:
            result['nodes'][ident]=deepcopy(existing.get(ident,node))
    for ident, claims in release.get('person_evidence', {}).items():
        ident=target(ident)
        if ident in result['nodes']:
            result['nodes'][ident]['reconciled_career_evidence']=deepcopy(claims)
    result.setdefault('sources',[]).append({'role':'reconciled_source_evidence','path':release['release_path'],'sha256':release['release_sha256']})
    result.setdefault('sourcecoverage',{})['reconciliation']=release.get('coverage',{})


def merge_bindings(resolved, nodes, prior_aliases, prior_groups, release, denied_pairs=()):
    """Do not replace an existing contradictory binding or user separation."""
    if not release:return resolved
    resolved=deepcopy(resolved)
    aliases={**prior_aliases,**resolved.get('identity_aliases',{})}
    def canonical(ident):
        seen=set()
        while ident in aliases:
            if ident in seen:raise ValueError('Identity alias cycle')
            seen.add(ident);ident=aliases[ident]
        return ident
    groups=[*prior_groups,*resolved.get('identity_groups',[])]
    protected={canonical(g['primary_id']) for g in groups}
    additions=defaultdict(list);held=[];reaffirmed=0
    for binding in release.get('identity_bindings',[]):
        source,target=binding['source'],canonical(binding['target'])
        existing=canonical(source)
        denied={frozenset((canonical(a),canonical(b))) for a,b in denied_pairs}
        if existing==target:
            reaffirmed+=1;continue
        if target not in nodes or existing!=source or existing in protected or frozenset((existing,target)) in denied:
            held.append({'source':source,'target':target,'reason':'EXISTING_BINDING_OR_SEPARATION_OR_TARGET_ABSENT'});continue
        aliases[source]=target;additions[target].append(binding);protected.add(target)
    if not additions:
        resolved['reconciled_evidence']={'new_bindings':0,'reaffirmed':reaffirmed,'held':held}
        return resolved
    by_root=defaultdict(set)
    for source in aliases:by_root[canonical(source)].add(source)
    existing_by_root={canonical(g['primary_id']):g for g in groups}
    output={g['primary_id']:g for g in resolved.get('identity_groups',[])}
    for target,bindings in additions.items():
        previous=existing_by_root.get(target)
        all_ids=sorted(by_root[target])
        source_ids=set(previous.get('source_person_ids',[]) if previous else [])
        source_ids.update(i.removeprefix('person:') if i.startswith('person:lda:') else i for i in [target,*all_ids])
        output[target]={'primary_id':target,'alias_ids':all_ids,'source_person_ids':sorted(source_ids),
            'display_name':nodes[target].get('label',target),'status':'REFERENCE_RESOLVED_SAME_PERSON',
            'resolution_status':'RECORD_RESOLVED_SAME_PERSON','basis':'RECORD_RESOLVED',
            'asserted_at':release['as_of'],'canonical_identity_promoted':False,
            'provenance':{'method':'HELD_SOURCE_EVIDENCE_RECONCILIATION','basis':'RECORD_RESOLVED',
                'path':release['release_path'],'sha256':release['release_sha256'],
                'bindings':bindings,'prior_provenance':previous.get('provenance') if previous else None,
                'employment_dates_from_quorum_or_lda_inferred':False}}
    resolved['identity_groups']=list(output.values())
    resolved['identity_aliases']={a:canonical(a) for a in aliases}
    resolved['unresolved_candidates']=[r for r in resolved.get('unresolved_candidates',[]) if len({canonical(i) for i in r.get('node_ids',[])})>1]
    resolved['reconciled_evidence']={'new_bindings':sum(map(len,additions.values())),'reaffirmed':reaffirmed,'held':held,'release_sha256':release['release_sha256']}
    return resolved


def bind_client_groups(index, release):
    index.client_to_group={};index.client_group_members=defaultdict(list);index.client_identity_proofs={};index.client_group_labels={}
    index.reconciled_filings=None
    if not release:return
    for group in release.get('client_groups',[]):
        label=group.get('label') or next((row.get('label') for row in group.get('evidence',{}).get('source_records',[]) if row.get('label')),None) or group['primary_id']
        index.client_group_labels[group['primary_id']]=label
        for ident in group['source_ids']:
            if ident in index.client_to_group and index.client_to_group[ident]!=group['primary_id']:
                raise ValueError('Client identity assigned to conflicting groups')
            index.client_to_group[ident]=group['primary_id']
            index.client_group_members[group['primary_id']].append(ident)
            index.client_identity_proofs[ident]={'primary_id':group['primary_id'],'source_ids':group['source_ids'],
                'method':group['method'],'evidence':group.get('evidence'),
                'path':release['release_path'],'sha256':release['release_sha256']}
    # Re-apply amendment precedence after client reconciliation. Otherwise the
    # old client ID's superseded roster could survive beside its replacement.
    if not index.client_to_group or not getattr(index,'database',None):return
    windows=defaultdict(dict)
    ids=sorted(index.client_to_group)
    with sqlite3.connect(index.database.as_uri()+'?mode=ro',uri=True) as con:
        for offset in range(0,len(ids),400):
            batch=ids[offset:offset+400]
            query='SELECT DISTINCT filing_uuid,firm_id,client_id,start,end_exclusive,posted FROM member WHERE client_id IN ('+','.join('?' for _ in batch)+')'
            for filing,firm,client,start,end,posted in con.execute(query,batch):
                windows[firm,index.client_to_group[client],start,end][filing]=posted
    allowed=set();suppressed=set()
    for filings in windows.values():
        newest=max(filings.values());latest=[f for f,p in filings.items() if p==newest]
        if len(latest)==1:allowed.update(latest)
        suppressed.update(set(filings)-allowed)
    index.reconciled_filings=allowed
    index.reconciled_filing_counts={'allowed':len(allowed),'suppressed_or_ambiguous':len(suppressed)}


def filing_allowed(index,row):
    return (getattr(index,'reconciled_filings',None) is None or row['client_id'] not in index.client_to_group
            or row['filing_uuid'] in index.reconciled_filings)


def bind_official_associations(index,release):
    aliases={a:ident for ident,node in index.nodes.items() for a in node.get('identity_alias_ids',[])}
    def canonical(ident):
        if ident.startswith('person:lda:lobbyist:'):
            ident=index.to_node.get(ident.removeprefix('person:'),ident)
        seen=set()
        while ident in aliases:
            if ident in seen:raise ValueError('Official roster alias cycle')
            seen.add(ident);ident=aliases[ident]
        return ident
    index.official_associations=[{**g,'member_ids':sorted({canonical(i) for i in g['member_ids']})}
                                for g in (release or {}).get('official_associations',[])]


def official_neighbors(index,person,options):
    if not options.get('include_review',True):return {}
    result={}
    for group in getattr(index,'official_associations',[]):
        members=group['member_ids']
        if person not in members:continue
        for other in members:
            if other==person or other not in index.nodes:continue
            hop={'source':person,'target':other,'relationship':group['relationship'],
                 'relationship_label':group['label'],'employer_id':group['organization_id'],
                 'employer_label':group['organization_label'],'temporal_status':'source_observation',
                 'evidence_summary':group['date_description'],'source_relation':True,'review_only':True,
                 'continuous_employment_asserted':False,'personal_familiarity_established':False,
                 'current_evidence':False,'start':group.get('start') or '', 'end_exclusive':group.get('end_exclusive') or '',
                 'cost':group['cost'],'cost_overlay_only':True,'provenance':group['provenance']}
            if other not in result or hop['cost']<result[other]['cost']:result[other]=hop
    return result
