#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Restore fifteen adjudicated association anchors to the primacy act universe.

This display-only producer consumes pinned official GovInfo BillStatus XML,
resolves sponsors and cosponsors against the live 533-member roster, and
publishes:

* one beneficiary tag for each adjudicated association anchor;
* an additive member-act membership sidecar containing resolved people only;
* a reconciliation receipt distinguishing official, resolved, and departed
  member links.

Extended 2026-08-10 to crypto/fintech association anchors; historical filename retained.
Extended 2026-10-07 with four telecom_primary association anchors (NCTA, USTelecom).
Departed members are recorded in the receipt and skipped from memberships.
The official rank authority and rank-scoring workbook are never modified.
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

from moneyball_core import write_text_atomic
from paths import ROOT as ROOT_TEXT


ROOT = Path(ROOT_TEXT)
BILLSTATUS = (
    ROOT / 'source_data' / 'legislation'
    / 'bank_anchor_billstatus'
)
TAGS = ROOT / 'bill_beneficiary_tags.ndjson'
MEMBERSHIPS = ROOT / 'data' / 'members' / 'bank_anchor_memberships.ndjson'
RECEIPT = ROOT / 'data' / 'legislation' / 'bank_anchor_reconciliation.json'
RANKS = ROOT / 'ranks_python.json'

COLLECTION_RECOVERY = 'bank-anchor-backfill'
# An ANCHORS entry may carry its own 'tagged' date; the original anchors do not.
DEFAULT_TAGGED = '2026-08-10'
MEMBERSHIP_SCHEMA = 'bank-anchor-membership-v1'

CLAWBACK_RATIONALE = (
    'operative clawback burden on failed-bank executives'
)
ILC_RATIONALE = (
    'ICBA-declared support for BHC-Act supervision of ILC parents'
)
ACRE_RATIONALE = (
    'ABA #1-priority rural-credit tax relief'
)

ANCHORS = {
    ('S.1045', '118'): {
        'xml': 'BILLSTATUS-118s1045.xml',
        'class': 'bank_primary',
        'authority': 'L0:manual-adjudication',
        'areas': ['Examination & Supervision'],
        'rationale': CLAWBACK_RATIONALE,
    },
    ('S.1790', '118'): {
        'xml': 'BILLSTATUS-118s1790.xml',
        'class': 'bank_primary',
        'authority': 'L0:manual-adjudication',
        'areas': ['Examination & Supervision'],
        'rationale': CLAWBACK_RATIONALE,
    },
    ('S.4050', '119'): {
        'xml': 'BILLSTATUS-119s4050.xml',
        'class': 'bank_primary',
        'authority': 'L0:manual-adjudication',
        'areas': ['Examination & Supervision'],
        'rationale': CLAWBACK_RATIONALE,
    },
    ('S.3538', '118'): {
        'xml': 'BILLSTATUS-118s3538.xml',
        'class': 'bank_primary',
        'authority': 'L1:association-position',
        'areas': ['Charter Modernization'],
        'rationale': ILC_RATIONALE,
    },
    ('S.3734', '119'): {
        'xml': 'BILLSTATUS-119s3734.xml',
        'class': 'bank_primary',
        'authority': 'L1:association-position',
        'areas': ['Charter Modernization'],
        'rationale': ILC_RATIONALE,
    },
    ('S.838', '119'): {
        'xml': 'BILLSTATUS-119s838.xml',
        'class': 'bank_primary',
        'authority': 'L1:association-position',
        'areas': ['Member Business Lending'],
        'rationale': ACRE_RATIONALE,
    },
    ('H.R.1822', '119'): {
        'xml': 'BILLSTATUS-119hr1822.xml',
        'class': 'bank_primary',
        'authority': 'L1:association-position',
        'areas': ['Member Business Lending'],
        'rationale': ACRE_RATIONALE,
    },
    ('S.2669', '118'): {
        'xml': 'BILLSTATUS-118s2669.xml',
        'class': 'crypto_primary',
        'authority': 'L0:manual-adjudication',
        'areas': ['Technology & Emerging'],
        'rationale': (
            'DAAMLA AML superstructure on digital assets; '
            'Blockchain Association formally opposed'
        ),
    },
    ('S.954', '119'): {
        'xml': 'BILLSTATUS-119s954.xml',
        'class': 'crypto_primary',
        'authority': 'L1:association-position',
        'areas': ['Technology & Emerging'],
        'rationale': 'strategic bitcoin reserve; industry-championed',
    },
    ('H.R.2032', '119'): {
        'xml': 'BILLSTATUS-119hr2032.xml',
        'class': 'crypto_primary',
        'authority': 'L1:association-position',
        'areas': ['Technology & Emerging'],
        'rationale': 'strategic bitcoin reserve; industry-championed',
    },
    ('H.R.9330', '119'): {
        'xml': 'BILLSTATUS-119hr9330.xml',
        'class': 'fintech',
        'authority': 'L1:association-position',
        'areas': ['Payments'],
        'rationale': 'AFC-backed EWA framework reintroduction',
    },
    ('H.R.3557', '118'): {
        'xml': 'BILLSTATUS-118hr3557.xml',
        'class': 'telecom_primary',
        'authority': 'L1:association-position',
        'areas': ['Technology & Emerging'],
        'rationale': 'NCTA and USTelecom-backed broadband permitting reform (American Broadband Deployment Act of 2023)',
        'tagged': '2026-10-07',
    },
    ('H.R.2289', '119'): {
        'xml': 'BILLSTATUS-119hr2289.xml',
        'class': 'telecom_primary',
        'authority': 'L1:association-position',
        'areas': ['Technology & Emerging'],
        'rationale': 'NCTA-led trade-association coalition letter urging floor action on the American Broadband Deployment Act of 2025',
        'tagged': '2026-10-07',
    },
    ('S.3587', '117'): {
        'xml': 'BILLSTATUS-117s3587.xml',
        'class': 'telecom_primary',
        'authority': 'L1:association-position',
        'areas': ['Technology & Emerging'],
        'rationale': 'NCTA-supported USDA ReConnect broadband funding (Connect Unserved Americans Act)',
        'tagged': '2026-10-07',
    },
    ('S.4821', '118'): {
        'xml': 'BILLSTATUS-118s4821.xml',
        'class': 'telecom_primary',
        'authority': 'L1:association-position',
        'areas': ['Technology & Emerging'],
        'rationale': 'USTelecom-endorsed faster federal-land broadband permits',
        'tagged': '2026-10-07',
    },
}

ACT_WEIGHT = {
    'bank_primary': 0.15,
    'crypto_primary': 0.15,
    'fintech': 0.15,
    'telecom_primary': 0.15,
}
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
    all_cosponsors = list(bill.findall('./cosponsors/item'))
    cosponsors = [
        node for node in all_cosponsors
        if not xml_text(node, 'sponsorshipWithdrawnDate')
    ]
    summaries = [
        clean_html(node.findtext('text'))
        for node in (
            bill.findall('./summaries/summary')
            + bill.findall('./summaries/billSummaries/item')
        )
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
        'withdrawn_links': len(all_cosponsors) - len(cosponsors),
    }


def display_key(record):
    prefix = {'HR': 'H.R.', 'S': 'S.'}.get(record['type'])
    if prefix is None:
        raise ValueError(
            'unsupported bank anchor measure type: %s' % record['type']
        )
    return prefix + str(int(record['number'])), record['congress']


def roster_resolver():
    congestion = read_json(ROOT / 'data' / 'engine' / 'congestion.json')
    names = [row['member'] for row in congestion]
    bioguide_map = {
        row.get('bioguide'): row['member']
        for row in congestion
        if row.get('bioguide')
    }
    rank_names = set((read_json(RANKS).get('rank') or {}))
    if (
        set(names) != rank_names
        or len(names) != 533
        or len(bioguide_map) != 533
    ):
        raise ValueError(
            'BillStatus bioguide map is not bound to the 533-rank roster'
        )
    return bioguide_map, rank_names


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


def resolve_person(node, bioguide_map):
    return bioguide_map.get(xml_text(node, 'bioguideId'))


def resolve_official_people(official, bioguide_map):
    candidates = [(official['sponsor'], 'sponsor')]
    for node in official['cosponsors']:
        role = (
            'original'
            if xml_text(node, 'isOriginalCosponsor').casefold() == 'true'
            else 'cosponsor'
        )
        candidates.append((node, role))

    resolved = []
    departed = []
    seen = set()
    for node, role in candidates:
        identity = (
            xml_text(node, 'bioguideId')
            or source_name(node)
            or xml_text(node, 'fullName')
        )
        if not identity:
            raise ValueError('official member link lacks an identity')
        if identity in seen:
            raise ValueError('duplicate official member link: %s' % identity)
        seen.add(identity)

        member = resolve_person(node, bioguide_map)
        if member:
            resolved.append((node, role, member))
        else:
            departed.append((node, role))
    return resolved, departed


def build():
    rank_hash_before = sha256(RANKS)
    existing = read_ndjson(TAGS)
    expected_missing = sorted(ANCHORS)

    existing_nonowned_keys = {
        (str(row['bill']).strip(), str(row['congress']).strip())
        for row in existing
        if row.get('collection_recovery') != COLLECTION_RECOVERY
    }
    missing_before = sorted(set(ANCHORS) - existing_nonowned_keys)
    if missing_before != expected_missing:
        raise ValueError(
            'anchor gap differs from the declared anchors: %s'
            % (missing_before,)
        )

    bioguide_map, rank_names = roster_resolver()
    all_memberships = []
    anchor_tags = []
    source_receipts = {}

    for key in expected_missing:
        config = ANCHORS[key]
        path = BILLSTATUS / config['xml']
        if not path.is_file():
            raise FileNotFoundError(
                'missing pinned BillStatus XML: %s' % path
            )

        official = parse_billstatus(path)
        if display_key(official) != key:
            raise ValueError(
                'BillStatus identity mismatch for %s: %s'
                % (path, display_key(official))
            )

        resolved_people, departed_people = resolve_official_people(
            official, bioguide_map
        )
        if not resolved_people:
            raise ValueError(
                'bank anchor has no resolvable 533-roster links: %s|%s'
                % key
            )

        sponsor_display = xml_text(official['sponsor'], 'fullName')
        areas = list(config['areas'])
        for node, role, member in resolved_people:
            all_memberships.append({
                'schema': MEMBERSHIP_SCHEMA,
                'member': member,
                'bioguide': xml_text(node, 'bioguideId'),
                'role': role,
                'bill': key[0],
                'congress': key[1],
                'title': official['title'],
                'sponsor': sponsor_display,
                'ncos': len(official['cosponsors']),
                'action': official['latest_action'],
                'summary': official['summary'],
                'url': official['url'],
                'areas': areas,
                'source': (
                    'official-govinfo-billstatus'
                    '+bank-anchor-adjudication'
                ),
                'source_billstatus_sha256': sha256(path),
                'sponsorship_date': (
                    official['introduced_date']
                    if role == 'sponsor'
                    else xml_text(node, 'sponsorshipDate')
                ),
            })

        role_credit = round(
            sum(ROLE_WEIGHT[role] for _, role, _ in resolved_people),
            3,
        )
        anchor_tags.append({
            'bill': key[0],
            'congress': key[1],
            'title': official['title'],
            'class': config['class'],
            'act_weight': ACT_WEIGHT[config['class']],
            'authority': config['authority'],
            'rationale': config['rationale'],
            'acu_position': '',
            'confidence': 0.85,
            'areas': areas,
            'roster_role_weight': role_credit,
            'member_links': len(resolved_people),
            'tagged': config.get('tagged', DEFAULT_TAGGED),
            'collection_recovery': COLLECTION_RECOVERY,
        })

        source_receipts['%s|%s' % key] = {
            'billstatus': str(path.relative_to(ROOT)),
            'billstatus_sha256': sha256(path),
            'billstatus_update_date': official['update_date'],
            'official_sponsor': sponsor_display,
            'official_links': (
                1 + len(official['cosponsors']) + official['withdrawn_links']
            ),
            'resolved_links': len(resolved_people),
            'departed_links': len(departed_people),
            'withdrawn_links': official['withdrawn_links'],
            'roster_role_weight': role_credit,
        }

    # Replace only the declared owned keys. This permits repeatable
    # official-source refreshes without duplicating or disturbing unrelated
    # beneficiary tags.
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
                row['bill'],
                row['congress'],
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
    missing_after = sorted(set(ANCHORS) - final_keys)
    rank_hash_after = sha256(RANKS)

    total_official_links = sum(
        row['official_links'] for row in source_receipts.values()
    )
    total_resolved_links = sum(
        row['resolved_links'] for row in source_receipts.values()
    )
    total_departed_links = sum(
        row['departed_links'] for row in source_receipts.values()
    )
    membership_anchor_keys = {
        (row['bill'], row['congress'])
        for row in all_memberships
    }

    gates = {
        'declared_collection_gaps_exactly_identified': (
            missing_before == expected_missing
        ),
        'all_declared_anchors_now_classified': not missing_after,
        'all_resolved_links_are_in_533_roster': all(
            row['member'] in rank_names for row in all_memberships
        ),
        'all_declared_anchor_membership_sets_nonempty': (
            membership_anchor_keys == set(ANCHORS)
        ),
        'official_link_accounting_closes': all(
            row['official_links']
            == row['resolved_links'] + row['departed_links']
            + row['withdrawn_links']
            for row in source_receipts.values()
        ),
        'resolved_receipt_total_matches_sidecar': (
            total_resolved_links == len(all_memberships)
        ),
        'official_rank_authority_byte_identical': (
            rank_hash_before == rank_hash_after
        ),
    }

    receipt = {
        'schema': 'bank-anchor-reconciliation-v1',
        'status': (
            'PASS_BANK_ANCHORS_RECOVERED'
            if all(gates.values())
            else 'FAIL_BANK_ANCHOR_RECOVERY'
        ),
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope': (
            'display-only bank-anchor collection repair; departed members '
            'are receipt-only; official rank scoring and workbook are '
            'intentionally unchanged'
        ),
        'counts': {
            'declared_bank_anchors': len(ANCHORS),
            'missing_before': len(missing_before),
            'missing_after': len(missing_after),
            'recovered_anchors': len(anchor_tags),
            'official_links': total_official_links,
            'resolved_links': total_resolved_links,
            'departed_links': total_departed_links,
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
            receipt,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ) + '\n',
        str(RECEIPT),
    )

    if not all(gates.values()):
        raise RuntimeError(
            'bank anchor recovery failed gates: %s'
            % sorted(
                name for name, passed in gates.items()
                if not passed
            )
        )

    print(
        'association anchors: %d/%d classified | '
        '%d resolved links | %d departed links skipped | '
        'rank hash unchanged'
        % (
            len(anchor_tags),
            len(ANCHORS),
            total_resolved_links,
            total_departed_links,
        )
    )
    return receipt


if __name__ == '__main__':
    build()
