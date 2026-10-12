# -*- coding: utf-8 -*-
"""Rebuild the accepted, display-only communication profile.

The five cue measurements are a locked result.  They are stored separately from this derived
artifact so routine data refreshes cannot silently re-estimate a failed/closed construct.  This
producer only (1) makes every facet's availability explicit and (2) recomputes the formal-role
visibility adjustment from current, auditable role inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from moneyball_core import dump_atomic
from paths import ROOT

LOCKED_PATH=os.path.join(ROOT,'fec','communication_style_v1.locked')
OUTPUT_PATH=os.path.join(ROOT,'communication_style.json')
FACETS=('warmth','excitement','activity','gregariousness','assertiveness')


def load(name):
    with open(os.path.join(ROOT,name),encoding='utf-8') as handle:
        return json.load(handle)


def sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def percentile(values,value):
    """Competition-rank percentile on [0,1], including average ties."""
    if len(values)<2:
        return 0.5
    below=sum(v<value for v in values)
    tied=sum(v==value for v in values)
    return round((below+(tied-1)/2)/(len(values)-1),3)


def freeze(source_path):
    """One-time migration of the accepted earlier-day measurements into a durable locked source."""
    with open(source_path,encoding='utf-8') as handle:
        source=json.load(handle)
    if source.get('schema')!='communication-style-v1' or len(source.get('members') or {})!=533:
        raise ValueError('bootstrap source is not the accepted 533-member communication-style-v1 artifact')
    members={}
    for name,row in source['members'].items():
        members[name]={k:row.get(k) for k in ('scope','raw_text','facets','elaboration')}
    frozen={
        'schema':'communication-style-locked-measurements-v1',
        'status':'LOCKED_ACCEPTED_MEASUREMENTS_DO_NOT_REFIT',
        'locked_result_source':source.get('locked_result_source'),
        'source_artifact_sha256':sha256_file(source_path),
        'member_n':len(members),
        'members':members,
    }
    dump_atomic(frozen,LOCKED_PATH,indent=1)
    return frozen


def role_adjustment(roster,dispositions,panel,roles):
    names=[name for name in roster if (dispositions.get(name) or {}).get('visibility_z') is not None]
    role_rows=roles.get('members') or {}
    X=[]; y=[]
    for name in names:
        chamber=str((roster[name] or {}).get('chamber') or '')
        nominal=float((role_rows.get(name) or {}).get('influence_bonus') or 0.0)
        leader=1.0 if (panel.get(name) or {}).get('committee_leader') else 0.0
        X.append([1.0,nominal,leader,1.0 if chamber.lower().startswith('s') else 0.0])
        y.append(float(dispositions[name]['visibility_z']))
    X=np.asarray(X,dtype=float); y=np.asarray(y,dtype=float)
    expected={}
    for i,name in enumerate(names):
        keep=np.arange(len(names))!=i
        penalty=np.eye(X.shape[1]); penalty[0,0]=0.0
        beta=np.linalg.solve(X[keep].T@X[keep]+penalty,X[keep].T@y[keep])
        expected[name]=float(X[i]@beta)
    residual={name:float(y[i])-expected[name] for i,name in enumerate(names)}
    adjusted={}
    for chamber in ('House','Senate'):
        group=[name for name in names if str((roster[name] or {}).get('chamber'))==chamber]
        vals=np.asarray([residual[name] for name in group],dtype=float)
        mean=float(vals.mean()); sd=float(vals.std(ddof=1)) or 1.0
        for name in group:
            adjusted[name]=(residual[name]-mean)/sd
    return names,expected,residual,adjusted


def build():
    locked=json.load(open(LOCKED_PATH,encoding='utf-8'))
    if locked.get('schema')!='communication-style-locked-measurements-v1':
        raise ValueError('locked communication measurement source missing or wrong schema')
    roster_list=load('data/engine/congestion.json')
    roster={row['member']:row for row in roster_list}
    dispositions=load('behavioral_dispositions.json').get('members') or {}
    panel=load('behavior_panel.json').get('members') or {}
    roles=load('data/members/formal_visibility_roles.json')
    ranks=load('ranks_python.json')
    if roles.get('schema')!='party-elite-influence-roles-v3':
        raise ValueError('data/members/formal_visibility_roles.json schema mismatch')
    if set(locked.get('members') or {})!=set(roster):
        raise ValueError('locked communication measurements do not match the current roster')

    names,expected,residual,adjusted=role_adjustment(roster,dispositions,panel,roles)
    observed_values=[float(dispositions[name]['visibility_z']) for name in names]
    adjusted_values=[adjusted[name] for name in names]
    role_rows=roles.get('members') or {}
    display_weights=ranks.get('formal_role_display_weight') or {}
    members={}
    for name in roster:
        frozen=locked['members'][name]
        measured=frozen.get('facets') or {}
        facets={}
        for facet in FACETS:
            if facet in measured and (measured[facet] or {}).get('percentile') is not None:
                facets[facet]=measured[facet]
            else:
                facets[facet]={
                    'z':None,'percentile':None,
                    'status':'unavailable_insufficient_evidence',
                    'reason':'locked v1 result did not produce a qualifying facet value',
                }
        exposure=None
        if name in adjusted:
            observed=float(dispositions[name]['visibility_z'])
            role=role_rows.get(name) or {}
            exposure={
                'observed_visibility_z':round(observed,6),
                'observed_visibility_percentile':percentile(observed_values,observed),
                'role_expected_visibility_z':round(expected[name],6),
                'observed_minus_role_expected':round(residual[name],6),
                'role_adjusted_visibility_z':round(adjusted[name],6),
                'role_adjusted_percentile':percentile(adjusted_values,adjusted[name]),
                'formal_role':role.get('office'),
                'formal_role_weight':float(role.get('influence_bonus') or 0.0),
                'formal_role_display_weight':float(display_weights.get(name) or 0.0),
                'formal_role_influence_bonus':0.0,
                'committee_leader':bool((panel.get(name) or {}).get('committee_leader')),
            }
        members[name]={
            'scope':frozen.get('scope') or 'observable public interview communication; not intrinsic personality',
            'raw_text':frozen.get('raw_text') or {},
            'facets':facets,
            'elaboration':frozen.get('elaboration'),
            'public_exposure':exposure,
            'extraversion':None,
            'extraversion_status':'unavailable_v1_construct_validation_miss',
        }

    input_names=(
        'behavioral_dispositions.json','behavior_panel.json','data/members/formal_visibility_roles.json',
        'ranks_python.json',
    )
    inputs={name:sha256_file(os.path.join(ROOT,name)) for name in input_names}
    inputs['fec/communication_style_v1.locked']=sha256_file(LOCKED_PATH)
    return {
        'schema':'communication-style-v1',
        'status':'ACTIVE_DISPLAY_ONLY',
        'member_n':len(members),
        'construct_contract':{
            'facets':'locked observable public interview cues and interaction behavior; unavailable values are explicit',
            'role_adjusted_visibility':'visibility residual after formal-office and committee-leadership expectation; still not sociability',
            'extraversion':'not produced; the locked v1 composite missed reliability, convergence, and aspect-coherence bars',
            'double_count_guard':'turn length and verbosity are reported together but never averaged as separate evidence',
        },
        'role_adjustment':{
            'method':'leave-one-out ridge on visibility_z using formal-role weight, committee-leader flag, and chamber',
            'ridge_lambda':1.0,
            'role_source_verified_on':roles['verified_on'],
            'role_source_valid_until':roles['valid_until'],
        },
        'locked_result_source':locked.get('locked_result_source'),
        'inputs_sha256':inputs,
        'members':members,
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bootstrap-from',help='one-time accepted communication_style.json migration')
    args=parser.parse_args()
    if args.bootstrap_from:
        freeze(os.path.abspath(args.bootstrap_from))
        print('locked accepted communication measurements: '+LOCKED_PATH)
        return
    doc=build()
    dump_atomic(doc,OUTPUT_PATH,indent=1)
    measured=sum(any((v.get('percentile') is not None) for v in row['facets'].values()) for row in doc['members'].values())
    print('communication_style.json: %d members, %d with measured facets; missing evidence explicit'%(
          len(doc['members']),measured))


if __name__=='__main__':
    main()
