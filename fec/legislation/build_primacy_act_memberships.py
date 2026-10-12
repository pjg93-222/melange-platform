#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Materialize missing role-bearing acts for the primacy member surface.

The member-desk workbook producer does not contain every adjudicated amendment
sponsor. This migration extracts only the missing memberships from a verified
closed bundle and writes a strict, reviewable sidecar. The runtime member-desk
producer consumes the sidecar additively and de-duplicates it against later
workbook/manual evidence.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
from pathlib import Path

from moneyball_core import write_text_atomic


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def read_tags(path):
    tags = set()
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = (str(row['bill']).strip(), str(row['congress']).strip())
        if key in tags:
            raise ValueError('duplicate beneficiary tag: %s|%s' % key)
        tags.add(key)
    return tags


def memberships(bundle):
    out = {}
    for member in bundle.get('members') or []:
        for bill in member.get('bills') or []:
            key = (
                str(bill.get('bill') or '').strip(),
                str(bill.get('congress') or '').strip(),
            )
            out.setdefault(key, []).append((member['name'], bill))
    return out


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(source_bundle, current_bundle, tag_path):
    source = memberships(read_json(source_bundle))
    current = memberships(read_json(current_bundle))
    tags = read_tags(tag_path)
    if set(source) != tags:
        raise ValueError(
            'verified source bundle is not closed to beneficiary tags: '
            'source=%d tags=%d' % (len(source), len(tags))
        )
    if not set(current).issubset(tags):
        raise ValueError('current bundle contains unclassified acts')
    missing = sorted(tags - set(current))
    rows = []
    for key in missing:
        linked = source.get(key) or []
        if len(linked) != 1:
            raise ValueError(
                '%s|%s must have exactly one source membership, got %d'
                % (key[0], key[1], len(linked))
            )
        member, bill = linked[0]
        if 'Amdt.' not in key[0] or bill.get('role') != 'sponsor':
            raise ValueError(
                '%s|%s is not a sponsor-only amendment gap' % key
            )
        rows.append({
            'schema': 'primacy-act-membership-v1',
            'member': member,
            'role': 'sponsor',
            'bill': key[0],
            'congress': key[1],
            'title': bill.get('title') or '',
            'sponsor': bill.get('sponsor') or member,
            'ncos': bill.get('ncos') or 0,
            'action': bill.get('action') or '',
            'summary': bill.get('summary') or '',
            'url': bill.get('url') or '',
            'areas': sorted(bill.get('areas') or []),
            'source': 'verified-merged-primacy-bundle',
            'source_bundle_sha256': sha256(source_bundle),
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-bundle', required=True)
    parser.add_argument(
        '--current-bundle', default='member_desk_bundle.json'
    )
    parser.add_argument('--tags', default='bill_beneficiary_tags.ndjson')
    parser.add_argument(
        '--output', default='data/members/primacy_act_memberships.ndjson'
    )
    args = parser.parse_args()
    rows = build(args.source_bundle, args.current_bundle, args.tags)
    text = ''.join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n'
        for row in rows
    )
    write_text_atomic(text, args.output)
    print(
        'primacy memberships: %d sponsor-only amendment gaps -> %s'
        % (len(rows), args.output)
    )


if __name__ == '__main__':
    main()
