# -*- coding: utf-8 -*-
"""Explicit activation wrapper for the deterministic, zero-API narrative renderer.

Narratives remain off by default.  This file repairs the deleted production path while requiring a
deliberate activation flag and a durable approval record; routine pipelines never invoke it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from members.b5_narratives_local_candidate import render_member
from moneyball_core import dump_atomic
from paths import ROOT


def build(basis_path,approval_record,only=None):
    with open(basis_path,encoding='utf-8') as handle:
        basis=json.load(handle)
    if basis.get('schema')!='profile-basis-v2' or basis.get('status')!='ACTIVE_CONSTRUCT_GUARDED':
        raise ValueError('active narratives require the validated profile-basis-v2 artifact')
    wanted={x.strip() for x in (only or '').split(',') if x.strip()}
    members={name:render_member(row) for name,row in sorted((basis.get('members') or {}).items())
             if not wanted or name in wanted}
    return {
        'schema':'member-narratives-v2-local',
        'status':'ACTIVE_LOCAL_ZERO_API',
        'activation_contract':'not run by the routine pipeline; requires explicit --activate and approval record',
        'approval_record':approval_record,
        'basis_sha256':hashlib.sha256(open(basis_path,'rb').read()).hexdigest(),
        'writer':'deterministic local templates; zero API/network/model cost',
        'members':members,
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--activate',action='store_true')
    parser.add_argument('--basis',default='data/members/profile_basis_v2.json')
    parser.add_argument('--out',default='member_narratives_v2.json')
    parser.add_argument('--approval-record',help='ticket, dated decision, or approval document reference')
    parser.add_argument('--only')
    args=parser.parse_args()
    if not args.activate:
        parser.error('narratives remain withheld; explicit --activate is required')
    if not args.approval_record:
        parser.error('--approval-record is required for activation')
    basis=args.basis if os.path.isabs(args.basis) else os.path.join(ROOT,args.basis)
    out=args.out if os.path.isabs(args.out) else os.path.join(ROOT,args.out)
    doc=build(basis,args.approval_record,args.only)
    dump_atomic(doc,out,indent=1)
    print('%s: %d active local narratives; approval %s'%(
          os.path.basename(out),len(doc['members']),args.approval_record))


if __name__=='__main__':
    main()
