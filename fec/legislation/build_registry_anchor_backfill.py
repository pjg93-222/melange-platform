#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Restore every declared ACU registry anchor to the primacy act universe.

The legislation search registry is a declared collection expectation.  Six
119th-Congress anchors were present in that registry but absent from both the
role workbooks and the member-desk bundle.  This producer consumes pinned
official GovInfo BillStatus XML, resolves every sponsor/cosponsor to the live
533-member roster, and publishes:

* one beneficiary tag for each missing anchor;
* an additive member-act membership sidecar; and
* a registry-vs-collected closure receipt covering all 55 declared anchors.

This is deliberately a display-primacy collection repair.  It never writes the
official rank authority or the rank-scoring workbook.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import datetime as dt
import hashlib
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from members.member_identity import Resolver
from moneyball_core import write_text_atomic
from paths import ROOT as ROOT_TEXT


ROOT = Path(ROOT_TEXT)
REGISTRY = (
    ROOT / 'source_data' / 'legislation'
    / 'acu_legislation_search_registry.json'
)
BILLSTATUS = (
    ROOT / 'source_data' / 'legislation'
    / 'registry_anchor_billstatus'
)
TAGS = ROOT / 'bill_beneficiary_tags.ndjson'
MEMBERSHIPS = ROOT / 'data' / 'members' / 'registry_anchor_memberships.ndjson'
RECEIPT = ROOT / 'data/legislation/registry_anchor_reconciliation.json'
RANKS = ROOT / 'ranks_python.json'

ANCHORS = {
    ('H.R.1', '119'): {
        'xml': 'BILLSTATUS-119hr1.xml',
        'class': 'cu_primary',
        'authority': 'L4:registry-CU-scoped',
        'rationale': 'anchor in credit_union_tax_status',
    },
    ('H.R.3380', '119'): {
        'xml': 'BILLSTATUS-119hr3380.xml',
        'class': 'cu_primary',
        'authority': 'L4:registry-CU-scoped',
        'rationale': 'anchor in examination_supervision',
    },
    ('S.427', '119'): {
        'xml': 'BILLSTATUS-119s427.xml',
        'class': 'cu_primary',
        'authority': 'L4:registry-CU-scoped',
        'rationale': 'anchor in examination_supervision',
    },
    ('S.1880', '119'): {
        'xml': 'BILLSTATUS-119s1880.xml',
        'class': 'shared',
        'authority': 'L4:registry-cross-industry',
        'rationale': 'anchor in cdfi_fund',
    },
    ('S.2704', '119'): {
        'xml': 'BILLSTATUS-119s2704.xml',
        'class': 'shared',
        'authority': 'L4:registry-cross-industry',
        'rationale': 'anchor in cdfi_fund',
    },
    ('S.3940', '119'): {
        'xml': 'BILLSTATUS-119s3940.xml',
        'class': 'shared',
        'authority': 'L4:registry-cross-industry',
        'rationale': 'anchor in cdfi_fund',
    },
}
ACT_WEIGHT = {'cu_primary': 1.0, 'shared': 0.5, 'oppose': -1.0}
ROLE_WEIGHT = {'sponsor': 1.0, 'original': 0.5, 'cosponsor': 0.25}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def read_ndjson(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding='utf-8-sig').splitlines()
        if line.strip()
    ]


def registry_rows():
    document = read_json(REGISTRY)
    rows = {}
    for category in document.get('categories') or []:
        label = str(category.get('label') or '').strip()
        for bill in category.get('known_bills') or []:
            key = (
                str(bill.get('number') or '').strip(),
                str(bill.get('congress') or '').strip(),
            )
            if key in rows:
                raise ValueError('duplicate registry anchor: %s|%s' % key)
            rows[key] = {
                **bill,
                'category': label,
            }
    if len(rows) != 55:
        raise ValueError(
            'registry anchor count drifted: expected 55, got %d' % len(rows)
        )
    return rows


def clean_html(value):
    text = re.sub(r'<[^>]+>', ' ', str(value or ''))
    return re.sub(r'\s+', ' ', html.unescape(text)).strip()


def xml_text(node, path, default=''):
    value = node.findtext(path)
    return str(value).strip() if value is not None else default


def parse_billstatus(path: Path):
    bill = ET.parse(path).getroot().find('bill')
    if bill is None:
        raise ValueError('BillStatus XML lacks bill node: %s' % path)
    sponsor_node = bill.find('./sponsors/item')
    if sponsor_node is None:
        raise ValueError('BillStatus XML lacks sponsor: %s' % path)
    cosponsors = list(bill.findall('./cosponsors/item'))
    summaries = [
        clean_html(node.findtext('text'))
        for node in bill.findall('./summaries/billSummaries/item')
        if clean_html(node.findtext('text'))
    ]
    return {
        'congress': xml_text(bill, 'congress'),
        'type': xml_text(bill, 'type').upper(),
        'number': xml_text(bill, 'number'),
        'title': xml_text(bill, 'title'),
        'introduced_date': xml_text(bill, 'introducedDate'),
        'latest_action': xml_text(bill, './latestAction/text'),
        'url': xml_text(bill, 'legislationUrl'),
        'update_date': xml_text(bill, 'updateDate'),
        'summary': summaries[-1] if summaries else '',
        'sponsor': sponsor_node,
        'cosponsors': cosponsors,
    }


def display_key(record):
    prefix = {'HR': 'H.R.', 'S': 'S.'}.get(record['type'])
    if prefix is None:
        raise ValueError('unsupported anchor measure type: %s' % record['type'])
    return prefix + str(int(record['number'])), record['congress']


def roster_resolver():
    congestion = read_json(ROOT / 'data' / 'engine' / 'congestion.json')
    names = [row['member'] for row in congestion]
    by_bio = {
        row['member']: row.get('bioguide')
        for row in congestion
        if row.get('bioguide')
    }
    rank_names = set((read_json(RANKS).get('rank') or {}))
    if set(names) != rank_names or len(names) != 533:
        raise ValueError('BillStatus resolver is not bound to the 533-rank roster')
    return Resolver(names, by_bio), rank_names


def source_name(node):
    label = xml_text(node, 'fullName')
    visible = re.sub(
        r'^(?:Sen|Rep|Del|Resident Commissioner)\.\s*',
        '',
        label.split('[', 1)[0].strip(),
        flags=re.I,
    )
    if ',' in visible:
        last, first = visible.split(',', 1)
        return (first.strip() + ' ' + last.strip()).strip()
    return visible


def resolve_person(node, resolver):
    label = xml_text(node, 'fullName')
    member = resolver.resolve(
        source_name(node),
        xml_text(node, 'bioguideId'),
    )
    if not member:
        raise ValueError('official member does not resolve to roster: %s' % label)
    return member


def build():
    rank_hash_before = sha256(RANKS)
    registry = registry_rows()
    existing = read_ndjson(TAGS)
    existing_keys = {
        (str(row['bill']).strip(), str(row['congress']).strip())
        for row in existing
        if row.get('collection_recovery') != 'registry-anchor-backfill'
    }
    missing_before = sorted(set(registry) - existing_keys)
    expected_missing = sorted(ANCHORS)
    if missing_before != expected_missing:
        raise ValueError(
            'registry gap differs from the six adjudicated anchors: %s'
            % (missing_before,)
        )

    resolver, rank_names = roster_resolver()
    all_memberships = []
    anchor_tags = []
    source_receipts = {}
    for key in expected_missing:
        config = ANCHORS[key]
        path = BILLSTATUS / config['xml']
        if not path.is_file():
            raise FileNotFoundError('missing pinned BillStatus XML: %s' % path)
        official = parse_billstatus(path)
        if display_key(official) != key:
            raise ValueError(
                'BillStatus identity mismatch for %s: %s'
                % (path, display_key(official))
            )
        registry_row = registry[key]
        sponsor = resolve_person(official['sponsor'], resolver)
        people = [(official['sponsor'], 'sponsor', sponsor)]
        for node in official['cosponsors']:
            role = (
                'original'
                if xml_text(node, 'isOriginalCosponsor').casefold() == 'true'
                else 'cosponsor'
            )
            people.append((node, role, resolve_person(node, resolver)))
        if len({member for _, _, member in people}) != len(people):
            raise ValueError('duplicate official member link on %s|%s' % key)
        areas = [registry_row['category']]
        for node, role, member in people:
            all_memberships.append({
                'schema': 'registry-anchor-membership-v1',
                'member': member,
                'bioguide': xml_text(node, 'bioguideId'),
                'role': role,
                'bill': key[0],
                'congress': key[1],
                'title': official['title'],
                'sponsor': sponsor,
                'ncos': len(official['cosponsors']),
                'action': official['latest_action'],
                'summary': official['summary'],
                'url': official['url'],
                'areas': areas,
                'source': 'official-govinfo-billstatus+acu-registry',
                'source_billstatus_sha256': sha256(path),
                'source_registry_sha256': sha256(REGISTRY),
                'sponsorship_date': (
                    official['introduced_date'] if role == 'sponsor'
                    else xml_text(node, 'sponsorshipDate')
                ),
            })
        beneficiary_class = (
            'oppose'
            if str(registry_row.get('acu_position') or '').upper() == 'OPPOSE'
            else config['class']
        )
        role_credit = round(
            sum(ROLE_WEIGHT[role] for _, role, _ in people), 3
        )
        anchor_tags.append({
            'bill': key[0],
            'congress': key[1],
            'title': official['title'],
            'class': beneficiary_class,
            'act_weight': ACT_WEIGHT[beneficiary_class],
            'authority': (
                'L1:registry-oppose'
                if beneficiary_class == 'oppose'
                else config['authority']
            ),
            'rationale': config['rationale'],
            'acu_position': str(
                registry_row.get('acu_position') or ''
            ).upper(),
            'confidence': 0.85,
            'areas': areas,
            'roster_role_weight': role_credit,
            'member_links': len(people),
            'tagged': '2026-07-29',
            'collection_recovery': 'registry-anchor-backfill',
        })
        source_receipts['%s|%s' % key] = {
            'billstatus': str(path.relative_to(ROOT)),
            'billstatus_sha256': sha256(path),
            'billstatus_update_date': official['update_date'],
            'registry_category': registry_row['category'],
            'acu_position': registry_row.get('acu_position'),
            'member_links': len(people),
            'roster_role_weight': role_credit,
        }

    # Idempotent replacement permits a later official refresh without ever
    # duplicating an anchor tag.
    retained = [
        row for row in existing
        if (
            str(row['bill']).strip(),
            str(row['congress']).strip(),
        ) not in ANCHORS
    ]
    tags = retained + sorted(
        anchor_tags,
        key=lambda row: (-row['roster_role_weight'], row['bill']),
    )
    tag_text = ''.join(
        json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n'
        for row in tags
    )
    membership_text = ''.join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n'
        for row in sorted(
            all_memberships,
            key=lambda row: (
                row['bill'], row['congress'],
                {'sponsor': 0, 'original': 1, 'cosponsor': 2}[row['role']],
                row['member'],
            ),
        )
    )
    write_text_atomic(tag_text, str(TAGS))
    write_text_atomic(membership_text, str(MEMBERSHIPS))

    final_keys = {
        (str(row['bill']).strip(), str(row['congress']).strip())
        for row in tags
    }
    missing_after = sorted(set(registry) - final_keys)
    rank_hash_after = sha256(RANKS)
    gates = {
        'registry_declares_exactly_55_unique_anchors': len(registry) == 55,
        'six_collection_gaps_exactly_identified': (
            missing_before == expected_missing
        ),
        'all_55_registry_anchors_now_classified': not missing_after,
        'all_official_member_links_resolve_to_533_roster': (
            all(row['member'] in rank_names for row in all_memberships)
        ),
        'all_six_anchor_membership_sets_nonempty': (
            len({
                (row['bill'], row['congress'])
                for row in all_memberships
            }) == 6
        ),
        'official_rank_authority_byte_identical': (
            rank_hash_before == rank_hash_after
        ),
    }
    receipt = {
        'schema': 'registry-anchor-reconciliation-v1',
        'status': (
            'PASS_REGISTRY_ANCHORS_RECOVERED'
            if all(gates.values())
            else 'FAIL_REGISTRY_ANCHOR_RECOVERY'
        ),
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope': (
            'display-only primacy collection repair; official rank scoring '
            'and workbook are intentionally unchanged'
        ),
        'counts': {
            'declared_registry_anchors': len(registry),
            'missing_before': len(missing_before),
            'missing_after': len(missing_after),
            'recovered_anchors': len(anchor_tags),
            'recovered_member_links': len(all_memberships),
            'classified_act_universe': len(tags),
        },
        'missing_before': [
            '%s|%s' % key for key in missing_before
        ],
        'missing_after': [
            '%s|%s' % key for key in missing_after
        ],
        'sources': source_receipts,
        'gates': gates,
        'outputs': {
            TAGS.name: sha256(TAGS),
            MEMBERSHIPS.name: sha256(MEMBERSHIPS),
        },
        'official_rank_sha256_before': rank_hash_before,
        'official_rank_sha256_after': rank_hash_after,
    }
    write_text_atomic(
        json.dumps(
            receipt, indent=2, sort_keys=True, ensure_ascii=False
        ) + '\n',
        str(RECEIPT),
    )
    if not all(gates.values()):
        raise RuntimeError(
            'registry anchor recovery failed gates: %s'
            % sorted(name for name, passed in gates.items() if not passed)
        )
    print(
        'registry anchors: 55/55 classified | 6 recovered | '
        '%d member links | rank hash unchanged'
        % len(all_memberships)
    )
    return receipt


if __name__ == '__main__':
    build()
