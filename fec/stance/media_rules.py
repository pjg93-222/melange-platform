# -*- coding: utf-8 -*-
"""Shared media category routing, normalization, and General-CU exclusion accounting.

All media producers and consumers use these names and word rules.  Text is routed to a policy
domain when the item itself supplies a subject.  Residual General Credit Union evidence remains
visible in an exclusion ledger, but is not projected into policy domains from a member's existing
profile: doing so would manufacture specificity and reinforce the score it was meant to measure.
"""
import os
import re

RAW_TO_STANCE = {
    'Examination & Supervision': 'Examination & Supervision',
    'Credit Union Capital Requirements': 'CU Capital Requirements',
    'NCUA Budget & Share Insurance Fund': 'NCUA Budget & Insurance',
    'Interchange': 'Interchange',
    'Payments': 'Payments',
    'Technology & Emerging Issues': 'Technology & Emerging',
    'Fraud Prevention': 'Fraud Prevention',
    'Data Privacy & Cybersecurity': 'Data Privacy & Cyber',
    'Charter Modernization & Governance': 'Charter Modernization',
    'Defense Credit Union Issues': 'Defense CU Issues',
    'Field of Membership': 'Field of Membership',
    'Credit Union Tax Status': 'CU Tax Status',
    'CDFI Fund': 'CDFI Fund',
    'CFPB Issues & Structural Reform': 'CFPB',
    'Credit Card & Consumer-Fee Mandates': 'Credit Card Mandates',
    'Telephone Consumer Protection Act': 'TCPA',
    'Member Business Lending': 'Member Business Lending',
    'Mortgage Lending & Housing': 'Mortgage & Housing',
}
STANCE_TO_RAW = {v: k for k, v in RAW_TO_STANCE.items()}
GENERAL = 'General Credit Union'
COMPETITIVE = 'CU-Bank Competitive Dynamics'

RAW_PATTERNS = {
    'Examination & Supervision': r'\bncua\b|examination|supervis|regulatory burden|call report|bank secrecy act|\bbsa\b|anti-money laundering|\baml\b|currency transaction report|\bctr\b|suspicious activity report|\bsar\b',
    'Credit Union Capital Requirements': r'capital requirement|risk.?based capital|net worth ratio',
    'NCUA Budget & Share Insurance Fund': r'share insurance|ncusif|ncua budget|central liquidity',
    'Interchange': r'interchange|swipe fee|routing mandate|durbin',
    'Payments': r'fednow|real.?time payments|faster payments|payments system|stablecoin|digital dollar|\bcbdc\b|earned wage',
    'Technology & Emerging Issues': r'fintech|artificial intelligence|\bai\b|crypto|digital asset|stablecoin|innovation',
    'Fraud Prevention': r'fraud|scam|elder financial|regulation e\b|zelle|p2p payment|bank secrecy act|\bbsa\b|anti-money laundering|\baml\b|currency transaction report|\bctr\b|suspicious activity report|\bsar\b',
    'Data Privacy & Cybersecurity': r'data breach|data privacy|data security|consumer privacy|financial privacy|cyber|gramm.?leach',
    'Charter Modernization & Governance': r'charter|de novo|governance',
    'Defense Credit Union Issues': r'military lending|defense credit union|military banking|military installation|servicemember',
    'Field of Membership': r'field of membership|common bond|underserved area',
    'CDFI Fund': r'\bcdfi\b|community development financial',
    'CFPB Issues & Structural Reform': r'\bcfpb\b|consumer financial protection|section 1071|section 1033|small business lending rule|open banking|overdraft|(?:bank|card|cfpb|financial|lender|account|deposit)[\s\S]{0,80}(?:junk|late) fees?|(?:junk|late) fees?[\s\S]{0,80}(?:bank|card|cfpb|financial|lender|account|deposit)',
    'Credit Card & Consumer-Fee Mandates': r'credit card competition|\bccca\b|credit card fee|credit card mandate|credit card late fee|card late fee|credit card interest rate',
    'Telephone Consumer Protection Act': r'\btcpa\b|telephone consumer protection|robocall',
    'Member Business Lending': r'member business lending|mbl cap|small.?dollar loan|business loan',
    'Mortgage Lending & Housing': r'mortgage|housing|trigger lead|\bhmda\b|federal home loan bank|gse',
    'Credit Union Tax Status': r'tax.?exempt|tax exemption|tax status|credit union tax|don.?t tax my credit union',
    COMPETITIVE: r'big bank|megabank|wall street bank|community bank|bank consolidation|industrial loan|de novo charter|bank lobby',
    GENERAL: r'credit union',
}
_RX = {k: re.compile(v, re.I) for k, v in RAW_PATTERNS.items()}
_ROAD_INTERCHANGE = re.compile(r'\b(?:road|highway|traffic|exit|ramp|transportation|intersection|ribbon cutting)\b', re.I)
_PAYMENT_INTERCHANGE = re.compile(r'\b(?:swipe|debit|credit card|merchant|payment|durbin|routing mandate|interchange fee)\b', re.I)
_CU_CONTEXT = re.compile(r'\bcredit unions?\b|\bcu\b|financial cooperative|don.?t tax my credit union', re.I)
_BIG_BANK_CONTEXT = re.compile(r'big bank|megabank|wall street bank|bank consolidation|industrial loan', re.I)

# Applied only to an otherwise-General item, so these broad words retain a CU
# context and do not turn generic congressional content into policy evidence.
_GENERAL_SUBJECT = {
    'Field of Membership': re.compile(r'underserved|financial inclusion|financial access|access to financial services|rural communit', re.I),
    'Member Business Lending': re.compile(r'small business|entrepreneur|business owner|commercial lending', re.I),
    'Mortgage Lending & Housing': re.compile(r'homebuyer|homeownership|home owner|affordable housing|housing market', re.I),
    'Fraud Prevention': re.compile(r'fraud|scam|identity theft', re.I),
    'Data Privacy & Cybersecurity': re.compile(r'privacy|cyber|data security|data breach', re.I),
    'Payments': re.compile(r'payment|transaction|money transfer', re.I),
    'Credit Union Tax Status': re.compile(r'tax', re.I),
}


# CROSSPOST_KEY_CHARS (N2, 2026-09-19; packet audits/laundry_list_closeout_20260918 FINDINGS.md
# A3).  ``normalized_text`` truncates at 120 characters and is ALSO the join key against
# tweet_stance_final.csv (make_member_desk.py ~1720/1737, build_media_blend.py ``_nrm``), so it
# cannot move without moving per-post stances -- and it does not move here.  The cross-post
# duplicate key is a different job and now has its own function.  Re-measured on the shipped
# social corpus (receipt fixM/m4_crosspost_key_census.out.txt): 8,647 of the 9,502 dedup-population
# rows exceed 120 normalised characters, so 445 of 1,434 duplicate groups -- 935 rows, 490 of them
# surplus -- were FALSE collisions, different statements sharing a boilerplate prefix.  At
# CROSSPOST_DEDUP=0 no score was wrong: 5,722 rows score under either key, row for row.  But 298 of
# the desk's 895 'also posted on ...' notes named the wrong statement (289 of them should not exist
# at all), and on the documented rollback (CROSSPOST_DEDUP=1, decision D-034) 289 rows would be
# dropped as duplicates of posts they are not.
# Default '0' = the whole statement.  CROSSPOST_KEY_CHARS=120 restores the legacy key byte for
# byte.  An empty key still means 'link-only post: no scorable text' under either value: the
# truncation of an empty normalisation is empty, so both call sites' emptiness test is unchanged.
CROSSPOST_KEY_CHARS = (os.environ.get('CROSSPOST_KEY_CHARS', '0') or '0').strip()
if not CROSSPOST_KEY_CHARS.isdigit():
    # Fail at import, before any producer does I/O: a dial that silently changes what counts
    # as one statement must never fall through to a default because it was misspelled (the
    # DESK_MEMBERSHIP_SOURCE pattern, make_member_desk.py ~232).
    raise ValueError("CROSSPOST_KEY_CHARS must be a non-negative integer ('0' = the whole "
                     "statement, '120' = the legacy key); got %r"
                     % os.environ.get('CROSSPOST_KEY_CHARS'))


def _normalized(text):
    text = re.sub(r'https?://\S+', '', str(text or '').lower())
    return re.sub(r'[^a-z0-9]', '', text)


def normalized_text(text):
    return _normalized(text)[:120]


def crosspost_key(text, chars=None):
    """The cross-post duplicate key for one statement.  PURE: no I/O, no mutable state."""
    limit = CROSSPOST_KEY_CHARS if chars is None else chars
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError('CROSSPOST_KEY_CHARS must be an integer; got %r' % (limit,))
    normalized = _normalized(text)
    return normalized[:limit] if limit > 0 else normalized


def hearing_tier_weight(tier):
    """Production hearing trust contract: adjudicated gold only.

    Learned non-gold rows remain visible in the sidecar and labeling queue, but receive zero Press
    weight until member attribution and stance are manually adjudicated.
    """
    return 1.0 if str(tier or '').strip().lower() == 'gold' else 0.0


def own_source_tier_weight(tier):
    """OOF-calibrated trust for speech/interview labels; unknown tiers fail closed."""
    return {
        'gold': 1.0,
        'unanimous': 0.9,
        'majority': 0.6,
        'review': 0.35,
    }.get(str(tier or '').strip().lower(), 0.0)


def _category_matches(category, text):
    if category not in _RX or not _RX[category].search(text):
        return False
    if category == 'Interchange' and _ROAD_INTERCHANGE.search(text) and not _PAYMENT_INTERCHANGE.search(text):
        return False
    if category == 'Credit Union Tax Status' and not _CU_CONTEXT.search(text):
        return False
    if category == COMPETITIVE and not (_CU_CONTEXT.search(text) or _BIG_BANK_CONTEXT.search(text)):
        return False
    return True


def route_raw_categories(text, declared=None):
    """Route an item to raw-sheet categories using precision-first shared rules."""
    text = str(text or '')
    declared = [str(c).strip() for c in (declared or []) if str(c).strip()]
    specific_declared = [c for c in declared if c in RAW_TO_STANCE or c == COMPETITIVE]
    supported_declared = [c for c in specific_declared if _category_matches(c, text)]
    if supported_declared:
        return list(dict.fromkeys(supported_declared))
    strict = [c for c in _RX if c != GENERAL and _category_matches(c, text)]
    if strict:
        return list(dict.fromkeys(strict))
    general_context = GENERAL in declared or _RX[GENERAL].search(text)
    if not general_context:
        return []
    broad = [c for c, rx in _GENERAL_SUBJECT.items() if rx.search(text)]
    return list(dict.fromkeys(broad)) if broad else [GENERAL]


def press_routing_decision(target='', title='', declared=None, body=''):
    """Return an auditable press-domain decision.

    The article-level API target is primary. The headline is next. Full text may confirm a declared
    category, but cannot spray every keyword it contains across domains. Declared tags alone never
    create policy evidence.
    """
    declared = [str(c).strip() for c in (declared or []) if str(c).strip()]
    declared_specific = [c for c in declared if c in RAW_TO_STANCE or c == COMPETITIVE]
    target_hits = route_raw_categories(target)
    target_specific = [c for c in target_hits if c != GENERAL]
    if target_specific:
        return {'categories': target_specific, 'basis': 'target',
                'declared': declared,
                'discarded_declared': [c for c in declared_specific if c not in target_specific]}
    title_hits = route_raw_categories(title)
    title_specific = [c for c in title_hits if c != GENERAL]
    if title_specific:
        return {'categories': title_specific, 'basis': 'title',
                'declared': declared,
                'discarded_declared': [c for c in declared_specific if c not in title_specific]}
    if target_hits == [GENERAL]:
        return {'categories': [GENERAL], 'basis': 'target',
                'declared': declared, 'discarded_declared': declared_specific}
    if title_hits == [GENERAL]:
        return {'categories': [GENERAL], 'basis': 'title',
                'declared': declared, 'discarded_declared': declared_specific}
    body_hits = route_raw_categories(body)
    confirmed = [c for c in declared_specific if c in body_hits]
    if confirmed:
        return {'categories': list(dict.fromkeys(confirmed)), 'basis': 'body-confirms-declared',
                'declared': declared,
                'discarded_declared': [c for c in declared_specific if c not in confirmed]}
    if GENERAL in declared and (_CU_CONTEXT.search(str(body or '')) or _CU_CONTEXT.search(str(title or ''))):
        return {'categories': [GENERAL], 'basis': 'body-general',
                'declared': declared, 'discarded_declared': declared_specific}
    return {'categories': [], 'basis': 'no-policy-domain',
            'declared': declared, 'discarded_declared': declared_specific}


def route_press_categories(target='', title='', declared=None, body=''):
    return press_routing_decision(target, title, declared, body)['categories']


def normalize_signed_extrema(values):
    """Normalize one policy domain while retaining relative magnitude on each side of zero.

    The media blend's p80 scaling makes press and social components commensurable.  This second,
    domain-level transform has a different job: it maps the strongest observed pro/anti values to
    +1/-1 without saturating every already-large raw value at those endpoints.
    """
    clean = {member: float(value or 0.0) for member, value in values.items()}
    pmax = max((value for value in clean.values() if value > 0.0), default=0.0)
    nmax = abs(min((value for value in clean.values() if value < 0.0), default=0.0))
    return {
        member: (value / pmax if value > 0.0 and pmax
                 else value / nmax if value < 0.0 and nmax
                 else 0.0)
        for member, value in clean.items()
    }


def general_exclusion_ledger(values):
    """Return nonzero General-CU evidence as an explicit, non-scoring audit ledger."""
    return {
        member: {
            'general': float(value),
            'applied_to_policy_domains': False,
            'reason': 'residual General Credit Union evidence has no evidenced policy domain',
        }
        for member, value in values.items() if abs(float(value or 0.0)) > 1e-12
    }
