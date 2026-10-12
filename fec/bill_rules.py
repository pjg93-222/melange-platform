# -*- coding: utf-8 -*-
"""Shared, auditable legislation routing rules.

The archive scorer, Champion corroboration gate, member desk, and audit console
must agree on category names and exclusions. Keep user-editable bill-specific
decisions in ``bill_overrides.csv``; keep only cross-cutting rules here.
"""
import csv
import os
import re

CURRENT_CONGRESS = int(os.environ.get('LEGISLATIVE_CURRENT_CONGRESS', '119'))
CONGRESS_DECAY_FACTOR = 0.8
DISAPPROVAL_RE = re.compile(r'disapprov(?:al|ing)', re.I)

CATEGORIES = (
    'Examination & Supervision', 'CU Capital Requirements',
    'NCUA Budget & Insurance', 'Interchange', 'Payments',
    'Technology & Emerging', 'Fraud Prevention', 'Data Privacy & Cyber',
    'Charter Modernization', 'Defense CU Issues', 'Field of Membership',
    'CU Tax Status', 'CDFI Fund', 'CFPB', 'Credit Card Mandates', 'TCPA',
    'Member Business Lending', 'Mortgage & Housing',
)

LABEL_TO_CANONICAL = {
    'CFPB Issues & Structural Reform': 'CFPB',
    'Charter Modernization & Governance': 'Charter Modernization',
    'Credit Union Capital Requirements': 'CU Capital Requirements',
    'Credit Card & Consumer-Fee Mandates': 'Credit Card Mandates',
    'Credit Union Tax Status': 'CU Tax Status',
    'Data Privacy & Cybersecurity': 'Data Privacy & Cyber',
    'Defense Credit Union Issues': 'Defense CU Issues',
    'Mortgage Lending & Housing': 'Mortgage & Housing',
    'NCUA Budget & Share Insurance Fund': 'NCUA Budget & Insurance',
    'Technology & Emerging Issues': 'Technology & Emerging',
    'Telephone Consumer Protection Act Issues': 'TCPA',
}

SECTION_KEYWORDS = (
    ('Examination & Supervision', 'examination'),
    ('CU Capital Requirements', 'capital'),
    ('NCUA Budget & Insurance', 'ncua'),
    ('Interchange', 'interchange'),
    ('Payments', 'payments'),
    ('Technology & Emerging', 'technology'),
    ('Fraud Prevention', 'fraud'),
    ('Data Privacy & Cyber', 'data privacy'),
    ('Charter Modernization', 'charter'),
    ('Defense CU Issues', 'defense'),
    ('Field of Membership', 'field of membership'),
    ('CU Tax Status', 'tax status'),
    ('CDFI Fund', 'cdfi'),
    ('CFPB', 'cfpb'),
    ('Credit Card Mandates', 'credit card'),
    ('TCPA', 'telephone'),
    ('Member Business Lending', 'member business'),
    ('Mortgage & Housing', 'mortgage'),
)

# Broad NDAA records are intentionally INCLUDED at the user's direction
# (2026-07-14). The remaining phrases are known keyword-scraper false positives.
EXCLUDED_TITLE_PHRASES = (
    'george floyd', 'save money on auto repair', 'auto repair transportation',
    'accessible, affordable internet', 'affordable internet for all',
    'health and location data', 'fourth amendment is not for sale',
    'app store accountability', 'safer gaming',
    'protecting military servicemembers', 'data from foreign adversaries',
    'cease orwellian surveillance',
)

# Some source rows carry only the ambiguous short title "SMART Act" and omit the
# disambiguating summary. Pin the two auto-repair bills by bill + Congress so
# every role row is excluded while financial/regulatory SMART Acts remain live.
EXCLUDED_BILL_CONGRESSES = {
    (('H', 3664), '117'): 'auto repair SMART Act',
    (('H', 1707), '118'): 'auto repair SMART Act',
}

# Exact, source-controlled corrections for unmistakably misfiled archive records. A live
# bill_overrides.csv row still wins, so these are safe defaults rather than locks.
BUILTIN_OVERRIDES = {
    (('H', 7733), '117'): {
        'Bill': 'H.R.7733', 'Congress': '117', 'Category': 'CDFI Fund', 'Include': '1',
        'Reason': 'CDFI Bond Guarantee Program Improvement Act',
    },
    (('S', 869), '118'): {
        'Bill': 'S.869', 'Congress': '118', 'Category': 'CDFI Fund', 'Include': '1',
        'Reason': 'CDFI Bond Guarantee Program Improvement Act',
    },
    (('H', 7036), '118'): {
        'Bill': 'H.R.7036', 'Congress': '118', 'Category': 'Data Privacy & Cyber', 'Include': '1',
        'Reason': 'Strengthening Cybersecurity for the Financial Sector',
    },
    (('H', 7022), '117'): {
        'Bill': 'H.R.7022', 'Congress': '117', 'Category': 'Data Privacy & Cyber', 'Include': '1',
        'Reason': 'Strengthening Cybersecurity for the Financial Sector',
    },
    (('S', 4698), '117'): {
        'Bill': 'S.4698', 'Congress': '117', 'Category': 'Data Privacy & Cyber', 'Include': '1',
        'Reason': 'Improving Cybersecurity of Credit Unions Act',
    },
}


def is_disapproval(title):
    """Return whether a title is a CRA resolution of disapproval (either drafting style)."""
    return bool(DISAPPROVAL_RE.search(str(title or '')))


def canonical_category(value):
    raw = str(value or '').strip()
    if not raw:
        return None
    if raw in CATEGORIES:
        return raw
    if raw in LABEL_TO_CANONICAL:
        return LABEL_TO_CANONICAL[raw]
    low = raw.lower()
    for category, keyword in SECTION_KEYWORDS:
        if keyword in low:
            return category
    return None


def excluded_title(title, summary=''):
    text = (str(title or '') + ' ' + str(summary or '')).lower()
    return next((phrase for phrase in EXCLUDED_TITLE_PHRASES if phrase in text), None)


def excluded_bill(bill, congress, title='', summary=''):
    pinned = EXCLUDED_BILL_CONGRESSES.get((bill_key(bill), _congress(congress)))
    return pinned or excluded_title(title, summary)


def bill_key(value):
    """Return the legacy-compatible (H|S, number) key used by bill_directions.csv."""
    match = re.match(r'\s*([HS])\.?\s*([RJ]?\.?\s*(?:Res\.?)?)\s*(\d+)', str(value or ''), re.I)
    return (match.group(1).upper(), int(match.group(3))) if match else None


def bill_type_key(value):
    """Return a type-aware bill key while leaving bill_key deliberately legacy-shaped.

    Known limitation: informal ``S.R.NNN`` parses as ``('S', 'R', NNN)``,
    exactly like plain ``S.NNN``.  Senate resolutions must be written
    ``S.Res.NNN`` to retain their distinct ``RES`` type.
    """
    match = re.match(
        r'\s*([HS])\.?\s*(?:(R|J\.?\s*Res|Con\.?\s*Res|Res)\.?\s*)?(\d+)',
        str(value or ''), re.I)
    if not match:
        return None
    type_token = re.sub(r'[^A-Za-z]', '', match.group(2) or 'R').upper()
    return (match.group(1).upper(), type_token, int(match.group(3)))


def key_collisions(identifiers):
    """Guard against bill_key ambiguity while leaving its legacy 2-tuple shape unchanged.

    ``?`` marks an identifier whose type could not be determined; such rows are
    treated as a distinct type on purpose so the guard fails closed.
    """
    groups = {}
    for identifier in identifiers:
        key = bill_key(identifier)
        if key is None:
            continue
        typed = bill_type_key(identifier)
        groups.setdefault(key, {})[str(identifier)] = typed[1] if typed else '?'
    return {
        key: sorted(names)
        for key, names in groups.items()
        if len(set(names.values())) > 1
    }


def evidence_key(value):
    """Return a collision-free key for either a formal bill or a manual evidence identifier."""
    parsed = bill_key(value)
    if parsed:
        return ('bill',) + parsed
    return ('manual', str(value or '').strip().casefold())


def is_letter_evidence(bill):
    """Return whether a manual evidence identifier is a sign-on/Dear Colleague letter."""
    return str(bill or '').strip().upper().startswith('LTR-')


def congress_decay(congress, current_congress=None):
    """Use the canonical bill schedule: 1.0 current, then 0.8 per Congress of age."""
    current = CURRENT_CONGRESS if current_congress is None else int(current_congress)
    try:
        source = int(float(congress))
    except (TypeError, ValueError):
        raise ValueError('letter evidence requires a numeric Congress, got %r' % (congress,))
    age = max(0, current - source)
    return round(CONGRESS_DECAY_FACTOR ** age, 12)


def evidence_decay(bill, congress, stored_weight=1.0):
    """Letters follow bill decay even if a stale manual row says 1.0."""
    if is_letter_evidence(bill):
        return congress_decay(congress)
    try:
        return float(stored_weight)
    except (TypeError, ValueError):
        return 1.0


def role_weight(role):
    value = canonical_role(role)
    if value == 'original-cosponsor':
        return 2.0
    if value == 'cosponsor':
        return 1.0
    if value == 'sponsor':
        return 3.0
    return 1.0


def canonical_role(role):
    value = str(role or '').strip().lower()
    if 'original' in value:
        return 'original-cosponsor'
    if 'cosponsor' in value or 'co-spons' in value:
        return 'cosponsor'
    if 'sponsor' in value:
        return 'sponsor'
    return value or 'other'


def preferred_role(roles):
    """Return the single strongest role for duplicate member/bill/domain rows."""
    priority = {'other': 0, 'cosponsor': 1, 'original-cosponsor': 2, 'sponsor': 3}
    canonical = [canonical_role(role) for role in roles]
    return max(canonical, key=lambda role: (priority.get(role, 0), role)) if canonical else 'other'


def _congress(value):
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value or '').strip()


def load_overrides(path):
    """Load {(bill_key, congress|'*'): row}; missing/empty files are valid."""
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            key = bill_key(row.get('Bill'))
            if not key:
                continue
            congress = _congress(row.get('Congress')) or '*'
            out[(key, congress)] = {k: (v or '').strip() for k, v in row.items()}
    return out


def override_for(overrides, bill, congress):
    key = bill_key(bill)
    if not key:
        return None
    congress_key = _congress(congress)
    return (overrides.get((key, congress_key)) or overrides.get((key, '*'))
            or BUILTIN_OVERRIDES.get((key, congress_key)))


def override_included(row):
    value = str((row or {}).get('Include', '')).strip().lower()
    return value not in {'0', 'false', 'no', 'exclude', 'excluded'}


# --------------------------------------------------------------------------
# adjudicated per-member membership withdrawals (2026-08-29)
# --------------------------------------------------------------------------
# Congress.gov / GovInfo BILLSTATUS reports a cosponsor who later withdrew via
# ``sponsorshipWithdrawnDate``.  The ledger must not carry that membership, but
# nothing could express the removal:
#
#   * ``bill_overrides.csv`` is keyed on (bill, congress) ALONE -- see
#     ``load_overrides`` above -- so ``Include=0`` there drops the act for EVERY
#     member, not for the one who withdrew.
#   * ``manual_bill_evidence.csv`` is an ADDITIVE evidence source read by a
#     dozen producers, several of them sha256-bound to a manifest.  A
#     ``Withdrawn`` COLUMN there would fail OPEN: every reader that had not
#     learned the new column would keep counting the row as evidence.
#
# So withdrawals live in their own artifact.  A producer that does not know
# about this file is unaffected by it; the one producer that must honour it
# (make_member_desk's pre-collapse ledger) reads it explicitly.
MEMBERSHIP_WITHDRAWAL_FIELDS = (
    'Bill', 'Congress', 'Member', 'Bioguide', 'JoinDate', 'WithdrawnDate',
    'Source', 'Note',
)


def withdrawal_act_key(bill, congress):
    """Return the act identity a withdrawal row matches a ledger row on.

    Type-aware on purpose (``H.R.1181`` and ``H.Res.1181`` are different acts),
    with a literal fallback so a non-bill evidence identifier such as
    ``LTR-119-CDFI-KIMCRAPO`` still keys cleanly instead of silently matching
    nothing.  Returns ``None`` only for an empty identifier.
    """
    typed = bill_type_key(bill)
    if typed is None:
        typed = re.sub(r'\s+', '', str(bill or '').strip().upper())
        if not typed:
            return None
    return (typed, _congress(congress))


def load_membership_withdrawals(path):
    """Load adjudicated (member, bill, congress) sponsorship withdrawals.

    A missing or empty file is VALID and yields ``[]``: with no file present
    every consumer behaves byte-for-byte as it did before this mechanism
    existed.  A row that cannot be normalized RAISES ``ValueError`` -- a
    malformed adjudication must fail CLOSED (halt the producer) rather than
    fail open into a membership the adjudication said to remove.

    Returns a list of dicts carrying the parsed row plus a precomputed
    ``key`` == ``withdrawal_act_key(Bill, Congress)`` and the 1-based ``row``
    ordinal for diagnostics.  Unknown columns are ignored, so the format stays
    additive.
    """
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding='utf-8-sig', newline='') as handle:
        for ordinal, raw in enumerate(csv.DictReader(handle), 1):
            row = {k: str(v or '').strip() for k, v in raw.items() if k}
            if not any(row.values()):
                continue                      # tolerate a trailing blank line
            bill = row.get('Bill') or ''
            congress = row.get('Congress') or ''
            member = row.get('Member') or ''
            key = withdrawal_act_key(bill, congress)
            if not bill or key is None or not key[1]:
                raise ValueError(
                    'membership withdrawal row %d has an unusable act: '
                    'Bill=%r Congress=%r' % (ordinal, bill, congress))
            if not member:
                raise ValueError(
                    'membership withdrawal row %d has no Member' % ordinal)
            if not row.get('WithdrawnDate'):
                # An adjudicated withdrawal must cite the date it is asserting;
                # a blank one is an unfinished edit, not a decision.
                raise ValueError(
                    'membership withdrawal row %d has no WithdrawnDate'
                    % ordinal)
            out.append({
                'row': ordinal,
                'bill': bill,
                'congress': key[1],
                'member': member,
                'bioguide': row.get('Bioguide') or '',
                'join_date': row.get('JoinDate') or '',
                'withdrawn_date': row.get('WithdrawnDate'),
                'source': row.get('Source') or '',
                'note': row.get('Note') or '',
                'key': key,
            })
    return out
