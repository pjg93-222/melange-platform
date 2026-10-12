# -*- coding: utf-8 -*-
"""Fail-closed validation for the locked communication-style artifact."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import ROOT

REQUIRED_FACETS={'warmth','excitement','activity','gregariousness','assertiveness'}


def sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def validate_input_hashes(artifact,root=ROOT):
    errors=[]
    recorded=artifact.get('inputs_sha256') or {}
    if not recorded:
        return ['inputs_sha256 is missing']
    for rel,want in recorded.items():
        path=Path(root)/rel
        if not path.is_file():
            errors.append('recorded input missing: '+rel)
            continue
        got=sha256_file(path)
        if got.lower()!=str(want).lower():
            errors.append('input hash mismatch: %s (recorded %s, live %s)'%(rel,str(want)[:12],got[:12]))
    return errors


def validate(artifact,roster,roles,root=ROOT,verify_hashes=True,applied_bonuses=None):
    members=artifact.get('members') or {}; errors=[]
    if artifact.get('schema')!='communication-style-v1': errors.append('schema mismatch')
    if artifact.get('status')!='ACTIVE_DISPLAY_ONLY': errors.append('status is not ACTIVE_DISPLAY_ONLY')
    if set(members)!=set(roster): errors.append('member roster mismatch')
    for name in roster:
        row=members.get(name) or {}
        if not all(k in row for k in ('scope','raw_text','facets','public_exposure','extraversion_status')):
            errors.append('row schema mismatch: '+name); continue
        facets=row.get('facets') or {}; present=set(facets)
        missing=REQUIRED_FACETS-present
        if missing: errors.append('missing required facets: %s (%s)'%(name,','.join(sorted(missing))))
        unknown=present-REQUIRED_FACETS
        if unknown: errors.append('unknown facets: %s (%s)'%(name,','.join(sorted(unknown))))
        for key,value in facets.items():
            value=value or {}; pct=value.get('percentile')
            unavailable=value.get('status')=='unavailable_insufficient_evidence'
            if pct is None and not unavailable: errors.append('malformed facet: %s/%s'%(name,key))
            if pct is not None and not 0<=float(pct)<=1: errors.append('facet percentile out of range: %s/%s'%(name,key))
        exposure=row.get('public_exposure')
        if exposure is not None and exposure.get('role_adjusted_percentile') is None:
            errors.append('malformed adjusted exposure: '+name)
    for name,role in (roles.get('members') or {}).items():
        # Role-adjusted visibility is display context; it must never masquerade as a rank bonus.
        want=float((applied_bonuses or {}).get(name,role.get('influence_bonus')) or 0.0)
        exposure=(members.get(name) or {}).get('public_exposure') or {}
        got=float(exposure.get('formal_role_display_weight') or 0.0)
        if abs(want-got)>1e-9: errors.append('formal-role display-weight mismatch: '+name)
        if float(exposure.get('formal_role_influence_bonus') or 0.0)!=0.0:
            errors.append('formal-role context leaked into rank bonus: '+name)
    if verify_hashes: errors.extend(validate_input_hashes(artifact,root))
    return errors


def main():
    with open(os.path.join(ROOT,'data', 'engine', 'congestion.json'),encoding='utf-8') as handle:
        roster=[m['member'] for m in json.load(handle)]
    with open(os.path.join(ROOT,'communication_style.json'),encoding='utf-8') as handle:
        artifact=json.load(handle)
    with open(os.path.join(ROOT,'data/members/formal_visibility_roles.json'),encoding='utf-8') as handle:
        roles=json.load(handle)
    with open(os.path.join(ROOT,'ranks_python.json'),encoding='utf-8') as handle:
        applied=json.load(handle).get('formal_role_display_weight') or {}
    errors=validate(artifact,roster,roles,applied_bonuses=applied)
    if errors:
        print('FATAL: communication_style.json validation failed: '+'; '.join(errors[:12]))
        return 1
    print('validated locked communication_style.json: %d members | schema %s | provenance current'%(
          len(artifact.get('members') or {}),artifact['schema']))
    return 0


if __name__=='__main__':
    sys.exit(main())
