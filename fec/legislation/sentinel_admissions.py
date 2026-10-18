"""Validated open-universe admissions shared by collection, measurement and display.

The frozen decision files remain historical authorities. New evidence is carried
in a separate, hash-bound generation with explicit machine policy provenance.
Missing generation is an exact no-op; corrupt or stale evidence fails closed.
"""
from __future__ import annotations
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import to_host  # noqa: E402

import copy
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import chain_transaction as CT  # noqa: E402  (fec/ is on sys.path via the line above)

SCHEMA = 'sentinel-admissions-v1'
RELATIVE = Path('data/legislation/sentinel_admissions.json')
# Sentinel source-outage rule (2026-10-11): the longest an admitted verdict may be carried past its last real
# verification while the House Code service is unavailable.
CARRY_FORWARD_MAX_DAYS = 30
CLASSES = {'cu_primary', 'shared', 'bank_primary', 'crypto_primary', 'fintech', 'telecom_primary', 'offtopic', 'oppose'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# D-122: the 2026-10-08 widening to 10 days was reverted to 8 after the 2026-10-11 refresh passed (audits/sentinel_admissions/20261011T182023Z; outages now carry forward under D-162).
# Self-healing phase 2 (Paul 2026-10-15): max_age_days is now the FRESH window, not a cliff. A generation (or an individual admission)
# past it is served as last-good and labelled STALE_LABELLED (document['freshness_status'], a stdout line, data/engine/source_freshness.json);
# a stamp from the future, a non-PASS or schema-invalid generation, and every evidence-hash / decision check still raise. The
# source-outage carry-forward limit (CARRY_FORWARD_MAX_DAYS, D-162) is a separate ratified bar and is unchanged.
def load(root, *, max_age_days=8):
    path = Path(root) / RELATIVE
    if not path.exists():
        return {'schema': SCHEMA, 'bills': {}, 'memberships': [], 'decisions': {}}
    document = json.loads(path.read_text(encoding='utf-8'))
    if document.get('schema') != SCHEMA or document.get('status') != 'PASS':
        raise ValueError('sentinel admissions are not a schema-valid PASS generation')
    stamp = datetime.fromisoformat(document['checked_at'].replace('Z', '+00:00'))
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    freshness = CT.age_status(age, max_age_days, 'sentinel admissions are outside their freshness window')
    for record in document['sources']:
        source = Path(to_host(record['path']))
        if not source.is_file() or digest(source) != record['sha256']:
            raise ValueError('sentinel admission evidence hash mismatch: ' + str(source))
    seen = set()
    for key, bill in document['bills'].items():
        decision = document['decisions'][key]
        if bill['key'] != key or decision['canonical_key'] != key or decision['disposition'] != 'include' or decision.get('confidence') != 'high':
            raise ValueError('sentinel bill admission identity/disposition mismatch')
        checked = datetime.fromisoformat(decision['checked_at'].replace('Z', '+00:00'))
        if decision.get('carried_forward') == 'source_unavailable':
            # Sentinel source-outage rule (2026-10-11): checked_at stays the last real verification, which must be at
            # most CARRY_FORWARD_MAX_DAYS old; the carry itself must be inside the normal window.
            if not -300 <= (datetime.now(timezone.utc)-checked).total_seconds() <= CARRY_FORWARD_MAX_DAYS*86400:
                raise ValueError('carried-forward bill admission is past its verification limit')
            checked = datetime.fromisoformat(decision['carried_at'].replace('Z', '+00:00'))
        if CT.age_status((datetime.now(timezone.utc)-checked).total_seconds(), max_age_days,
                         'individual bill admission is stale') == CT.STALE_LABELLED:
            freshness = CT.STALE_LABELLED
        if decision['beneficiary_class'] not in CLASSES or not decision['categories']:
            raise ValueError('sentinel bill has no beneficiary/category classification')
        for category in decision['categories']:
            if type(category['direction']) is not int or category['direction'] not in (-1, 0, 1):
                raise ValueError('sentinel bill has no explicit scoring direction')
            if not category.get('rationale') or not category.get('quote'):
                raise ValueError('sentinel direction lacks substantive source provenance')
        from legislation.govinfo_discovery_fallback import _parse_bill_text
        from legislation.refresh_sentinel_admissions import validate_decision, statute_text
        import bill_rules as BR
        registry = json.loads((Path(root)/'source_data/legislation/acu_legislation_search_registry.json').read_text(encoding='utf-8'))
        categories = {BR.canonical_category(r['id']) for r in registry['categories']}
        evidence = {r['url']: r for r in decision['evidence']}
        texts = {}
        for category in decision['categories']:
            source = evidence[category['source_url']]
            if source not in document['sources'] or digest(to_host(source['path'])) != source['sha256']:
                raise ValueError('decision evidence is not bound to the generation')
            raw=Path(to_host(source['path'])).read_bytes()
            texts[source['url']] = (statute_text(raw,source['title'],source['section']) if source.get('kind')=='statute'
                                    else _parse_bill_text(raw,url=source['url'],canonical_key=key))
        validate_decision(decision,key,texts,categories)
        if sorted(bill['areas']) != sorted(c['category'] for c in decision['categories']):
            raise ValueError('bill categories differ from its decision')
    for row in document['memberships']:
        key = (row['repository_key'], row['bioguide'], row['role'])
        if key in seen or key[0] not in document['bills'] or row['role'] not in ('sponsor', 'original', 'cosponsor'):
            raise ValueError('invalid or duplicate sentinel membership')
        seen.add(key)
    validate_membership_projection(document)
    if freshness == CT.STALE_LABELLED:
        document['freshness_status'] = freshness   # in-memory label only; never part of the stored generation
    CT.note_freshness(root, 'sentinel_admissions', freshness, checked_at=document['checked_at'], window_days=max_age_days,
                      producer='fec/legislation/sentinel_admissions.py')
    return document


def validate_membership_projection(document):
    """Every scored role, category and withdrawal must match its admitted bill."""
    expected = {}
    for key,bill in document['bills'].items():
        people=[('sponsor',bill['sponsor'])]
        people += [('original',p) for p in bill['original_cosponsors']]
        people += [('cosponsor',p) for p in bill['cosponsors']]
        for role,person in people:
            identity=(key,person.get('bioguide'),role)
            if identity in expected:
                raise ValueError('Duplicate official admission membership')
            expected[identity]=(bill,person)
    unresolved={(r['key'],r.get('bioguide'),r['role']) for r in document.get('unresolved_members',[])}
    actual=set()
    for row in document['memberships']:
        identity=(row['repository_key'],row['bioguide'],row['role'])
        if identity not in expected or identity in actual:
            raise ValueError('Membership is absent from the admitted official bill or duplicated')
        actual.add(identity)
        bill,person=expected[identity]
        if (row['bill']!=bill['display'] or str(row['congress'])!=str(bill['congress'])
                or sorted(row['areas'])!=sorted(bill['areas'])
                or row['withdrawn']!=bool(person.get('withdrawn_date'))
                or (row.get('withdrawn_date') or '')!=(person.get('withdrawn_date') or '')):
            raise ValueError('Membership scoring fields disagree with official admission')
    if actual & unresolved or actual | unresolved != set(expected):
        raise ValueError('Admission membership coverage is incomplete or inconsistent')


def augment_bundle(bundle, document):
    """Use identical seed-bearing fields in the collector and member producer."""
    if not document.get('memberships') and not document.get('bill_catalog'):
        return bundle
    result = copy.deepcopy(bundle)
    by_name = {m['name']: m for m in result.get('members', [])}
    # Replace this generation's bill projections, including withdrawn joins.
    # The historical decision files and unrelated acts are never changed.
    managed = {(b['display'],str(b['congress'])) for b in document.get('bill_catalog',document['bills']).values()}
    for member in by_name.values():
        member['bills'] = [b for b in member.get('bills',[]) if (b.get('bill'),str(b.get('congress'))) not in managed]
    for row in document['memberships']:
        if row.get('withdrawn'):
            continue
        if row['member'] not in by_name:
            raise ValueError('admitted member is absent from the current roster: ' + row['member'])
        member = by_name[row['member']]
        existing = member.setdefault('bills', [])
        identity = (row['bill'], str(row['congress']))
        if any((b.get('bill'), str(b.get('congress'))) == identity for b in existing):
            continue
        existing.append({k: row[k] for k in ('bill','congress','role','title','sponsor','summary','action','ncos','url','areas')})
    return result


def effective_tags(tags, document):
    result = dict(tags)
    active={r['repository_key'] for r in document.get('memberships',[]) if not r.get('withdrawn')}
    for key, bill in document.get('bills', {}).items():
        if 'memberships' in document and key not in active:
            continue
        act = bill['display'] + '|' + str(bill['congress'])
        if act in result:
            continue  # existing ratified classification wins
        decision = document['decisions'][key]
        result[act] = {'bill': bill['display'], 'congress': str(bill['congress']),
                       'title': bill['title'], 'class': decision['beneficiary_class'],
                       'authority': 'automated-official-evidence-policy',
                       'rationale': decision['rationale'], 'acu_position': '',
                       'automated_category_directions': [c['direction'] for c in decision['categories']],
                       'areas': [c['category'] for c in decision['categories']]}
    return result


def scoring_directions(document):
    return {(b['display'], str(b['congress']), c['category']): c['direction']
            for key, b in document.get('bills', {}).items()
            for c in document['decisions'][key]['categories']}


def append_to_desk(bills, document):
    """Supply new measured facts to the ordinary collapse/deduplication path."""
    count = 0
    for row in document.get('memberships', []):
        if row.get('withdrawn'):
            continue
        for category in row['areas']:
            bills.setdefault(row['member'], []).append({
                **{k: row[k] for k in ('bill','congress','role','title','sponsor','summary','action','ncos','url')},
                'area': category,
            })
            count += 1
    return count


def remove_managed_desk_rows(bills,document):
    managed={(b['display'],str(b['congress'])) for b in document.get('bill_catalog',document.get('bills',{})).values()}
    count=0
    for name,rows in bills.items():
        keep=[r for r in rows if (r.get('bill'),str(r.get('congress'))) not in managed]
        count+=len(rows)-len(keep);bills[name]=keep
    return count


def membership_eligible(row,document,area=None):
    key=row['repository_key']
    if key not in document.get('bill_catalog',document.get('bills',{})): return True
    if key not in document['bills']: return False
    return any(r['repository_key']==key and r['bioguide']==row['bioguide'] and r['role']==row['role']
               and not r.get('withdrawn') and (area is None or area in r['areas']) for r in document['memberships'])
