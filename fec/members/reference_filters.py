# -*- coding: utf-8 -*-
"""ADMISSION FILTERS for the signed member-reference layer (Paul-approved 2026-09-19).

Three rules that decide whether a matched mention is a REFERENCE at all. They are measured,
not proposed: audits/laundry_list_closeout_20260918/FINDINGS.md section B, with the receipts
in laneB/receipts (02c re-derives the shipped artifact 8,017/8,017 edges, 03b the domain
census, 05b the roster detector, 06 the duplicate windows) and F2/receipts (graded precision
40 of 40 on the roster detector, 0 of 40 missed).

  (a) OWN-DOMAIN GATE, PRESS pass only  [FINDINGS B6]
      A press headline is the speaker's own words only when the article sits on the
      speaker's own official domain. 8 of 42 PRESS mentions came off a third-party host
      (all americascreditunions.org) and one article minted the 5 false edges of the
      'Magaziner cluster'. The typed PRESS_OFFICIAL_MENTION feed is NOT in scope: it
      measured 0 violations.
  (b) ROSTER / SIGNATURE-BLOCK EXCLUSION  [FINDINGS B4]
      A roll-call roster, a cloture signature block and a co-sponsor list name colleagues
      without referring to them. 749 mentions, 502 scored, 78 shipped edges.
  (c) IDENTICAL WINDOWS COUNT ONCE  [FINDINGS B5]
      Two copies of one window carry the same classification (the cache key hashes
      a, b, w[:400]) and add no information, yet each used to increment n_scored -- the
      consumers' drawing floor. 157 edges, 170 surplus windows, 53 drawn edges standing on
      the copies alone.

THIS MODULE IS PURE. No data, no filesystem, no environment, no imports from the production
tree: fec/members/member_reference_layer.py executes its whole body on import and writes the
live data tree, so the rules live here where a test can import them. Every dial is read in
the producer, once, at top level; every function here is told what to do and returns its own
exclusion census so the artifact's accounting can close."""
import collections, re, unicodedata

DIALS = ('REF_PRESS_OWN_DOMAIN', 'REF_ROSTER_FILTER', 'REF_IDENTICAL_ONCE')
EXCLUSION_KEYS = ('press_third_party_domain', 'roster_list_window', 'identical_window_repeat')
RULES = {
    'press_third_party_domain': "PRESS pass: the article host is not the speaker's own "
                                ".house.gov/.senate.gov domain (ratified 2-class rule)",
    'roster_list_window': 'the window is a roll-call roster or signature block: >=%d distinct '
                          'roster surnames AND >=%d%% of its tokens are roster surnames',
    'identical_window_repeat': 'a window identical to an earlier window on the same edge after '
                               'whitespace normalisation; counted once in n_scored and the '
                               'pos/neg/neu tallies, still counted in n_raw',
}

# ---------------------------------------------------------------- (a) own-domain gate
def own_official_domain(url):
    """True when the url is a member's own official site.

    The predicate is the owner-ratified 2-class domain rule, restated VERBATIM from
    fec/stance/resolve_attribution_domain.py:45-47 (user rule 2026-07-04: "cross reference
    the link for it - if it is from a member's website its theirs, anything else is third
    party"). Restated rather than imported because that module reads
    data/stance/attribution_gold_sheet.csv at import time and rewrites it under --write."""
    ul = (url or '').lower()
    return ('.house.gov' in ul or '.senate.gov' in ul)

# ------------------------------------------------- (b) roster / signature-block detector
ROSTER_MIN_DISTINCT = 10      # lane B detector D-P3b, receipts/05b_p3_graded.py:135-139
ROSTER_MIN_RATIO = 0.35
ROSTER_MIN_TOKENS = 12
_TOKENS = re.compile(r'[^A-Za-z\'\-]+')

def _norm(s):
    """member_reference_layer.py:43-45's norm(), so a surname matches the producer's pool."""
    s = unicodedata.normalize('NFD', s or ''); s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", '', s.lower()).strip()

def roster_surnames(member_names):
    """The producer's surname pool: the last token of each normalised roster name."""
    out = set()
    for m in member_names:
        parts = _norm(m).split()
        if parts: out.add(parts[-1])
    return frozenset(out)

def is_roster_list(window, surnames, min_distinct=ROSTER_MIN_DISTINCT,
                   min_ratio=ROSTER_MIN_RATIO, min_tokens=ROSTER_MIN_TOKENS):
    """Lane B's detector, reproduced exactly so its graded precision transfers.

    Tokenisation ignores every non-letter, so the producer's whitespace-collapsed window
    (member_reference_layer.py:132) and the raw slice lane B measured give the same answer."""
    toks = [t for t in _TOKENS.split(window or '') if len(t) > 1]
    if len(toks) < min_tokens: return False
    hit = [t for t in toks if _norm(t) in surnames]
    return len(set(_norm(t) for t in hit)) >= min_distinct and len(hit) / len(toks) >= min_ratio

# ------------------------------------------------------------ (c) identical windows once
_WS = re.compile(r'\s+')

def normalized_window(w):
    """The comparison form: collapse runs of whitespace, strip the ends."""
    return _WS.sub(' ', w or '').strip()

def collapse_identical(rows, enabled=True):
    """Keep the first of each (edge, normalised window); return (kept, n_excluded).

    Per EDGE, across sources -- two copies of one statement are one piece of evidence
    whichever feeds carried it. Disabled, this returns the same rows, in order, unchanged."""
    if not enabled: return list(rows), 0
    seen = set(); kept = []; dropped = 0
    for r in rows:
        k = (r['a'], r['b'], normalized_window(r.get('w')))
        if k in seen: dropped += 1; continue
        seen.add(k); kept.append(r)
    return kept, dropped

# ------------------------------------------------------------------- the admission pass
def apply_admission_filters(refs, surnames, press_own_domain=True, roster_filter=True):
    """Drop every mention that is not a reference; return (admitted, exclusion census).

    Runs on the raw mention list, so an excluded window is out before n_raw, before the
    per-dyad cap and before the scoring queue -- --score never pays for one. Filters (a)
    and (b) remove mentions; filter (c) is applied to the scored queue by
    collapse_identical(), because a repeat is still a raw mention."""
    excluded = collections.Counter()
    kept = []
    for r in refs:
        if press_own_domain and r.get('src') == 'PRESS' and not own_official_domain(r.get('url')):
            excluded['press_third_party_domain'] += 1; continue
        if roster_filter and is_roster_list(r.get('w'), surnames):
            excluded['roster_list_window'] += 1; continue
        kept.append(r)
    return kept, excluded

def admission_filters_block(states, excluded):
    """The artifact's additive top-level block: which dials ran, and what each one excluded."""
    counts = {k: int((excluded or {}).get(k, 0)) for k in EXCLUSION_KEYS}
    return {'schema': 'member-reference-admission-filters-v1',
            'dials': {d: ('1' if states.get(d) else '0') for d in DIALS},
            'defaults': {d: '1' for d in DIALS},
            'excluded': counts,
            'excluded_total': sum(counts.values()),
            'rules': {'press_third_party_domain': RULES['press_third_party_domain'],
                      'roster_list_window': RULES['roster_list_window']
                      % (ROSTER_MIN_DISTINCT, int(ROSTER_MIN_RATIO * 100)),
                      'identical_window_repeat': RULES['identical_window_repeat']},
            'provenance': 'audits/laundry_list_closeout_20260918 FINDINGS.md B4/B5/B6; '
                          'Paul-approved 2026-09-19. Each default is the admitted-set rule; '
                          "'0' restores the prior behaviour exactly."}
