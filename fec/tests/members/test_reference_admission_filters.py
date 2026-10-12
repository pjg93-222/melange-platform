# -*- coding: utf-8 -*-
"""ADMISSION FILTERS for the signed member-reference layer (Paul-approved 2026-09-19).

WHAT IS UNDER TEST. fec/members/reference_filters.py holds three admission rules the
reference layer had no guard for, each measured in audits/laundry_list_closeout_20260918
(FINDINGS.md section B, receipts in laneB/receipts and F2/receipts):

  B6  own-domain gate on the PRESS pass  -- 8 of 42 PRESS mentions came off a third-party
      host (all americascreditunions.org); the 'Magaziner cluster' is 5 false edges minted
      from one article.  The ratified 2-class domain rule already existed
      (fec/stance/resolve_attribution_domain.py:45-47) and was never called here.
  B4  roll-call / signature-block windows read as references -- 749 mentions, 502 scored,
      78 shipped edges.  Graded precision 40 of 40, recall probe 0 of 40 missed (F2).
  B5  byte-identical windows counted more than once -- 157 edges, 170 surplus windows;
      53 drawn edges sit above the consumers' n_scored>=2 floor only because of the copies.

WHY IT IS A SEPARATE MODULE. fec/members/member_reference_layer.py cannot be imported:
like every production script here it executes its whole body on import and writes the live
data tree.  The rules therefore live in a pure, data-free, side-effect-free module that a
test can import, and this suite reads the PRODUCER as text to pin the wiring -- the same
split test_crosspost_scoring.py uses for the desk/blend dedup rule.

Synthetic fixtures only: no roster, no database, no artifact, no MONEYBALL_ROOT.  Every
filter is driven in BOTH dial positions, and the '0' position is asserted to be an exact
identity so a rollback is a dial flip rather than a revert."""
import os, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # run_tests.py decodes this pipe as utf-8

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
FEC = os.path.join(ROOT, 'fec')
sys.path.insert(0, FEC)
from members import reference_filters as RF

P = F = 0
def ok(name, cond, detail=''):
    global P, F
    if cond: P += 1; print('  OK   ' + name)
    else:    F += 1; print('  FAIL ' + name + ('  :: ' + str(detail) if detail else ''))

PRODUCER = os.path.join(FEC, 'members', 'member_reference_layer.py')
HELPER = os.path.join(FEC, 'members', 'reference_filters.py')
RATIFIED = os.path.join(FEC, 'stance', 'resolve_attribution_domain.py')
SRC = open(PRODUCER, encoding='utf-8').read()
HELPER_SRC = open(HELPER, encoding='utf-8').read()

# A synthetic roster: 12 members, 12 distinct surnames, none of them a real member.
ROSTER = ['Ada Alpher', 'Ben Bravo', 'Cy Charlie', 'Dee Delta', 'Eli Echo', 'Fay Foxtrot',
          'Gus Golf', 'Hal Hotel', 'Ida India', 'Jay Juliet', 'Kim Kilo', 'Lou Lima']
SUR = RF.roster_surnames(ROSTER)

print('[a) own-domain gate -- the ratified 2-class rule, restated not imported]')
ok('speaker .house.gov article is own words', RF.own_official_domain('https://hill.house.gov/news/a'))
ok('.senate.gov article is own words', RF.own_official_domain('https://www.crapo.senate.gov/media/b'))
ok('trade-association host is third party (the 8 measured PRESS violations)',
   not RF.own_official_domain('https://www.americascreditunions.org/news/x'))
ok('news outlet is third party', not RF.own_official_domain('https://www.cnn.com/2025/01/01/x'))
ok('case is ignored', RF.own_official_domain('HTTPS://HILL.HOUSE.GOV/News'))
ok('missing url is not own words', not RF.own_official_domain('') and not RF.own_official_domain(None))
# The ratified rule is a substring test on '.house.gov'/'.senate.gov'.  It is restated here
# EXACTLY, including the consequence that a bare apex host does not match -- restating a
# ratified rule means reproducing it, not improving it.
ok('bare apex host does not match, exactly as the ratified rule reads',
   not RF.own_official_domain('https://house.gov/news/a'))
_RULE = "('.house.gov' in ul or '.senate.gov' in ul)"
ok('the helper carries the ratified predicate verbatim', _RULE in HELPER_SRC)
ok('the ratified source still reads that way (fec/stance/resolve_attribution_domain.py)',
   _RULE in open(RATIFIED, encoding='utf-8').read())
ok('the ratified module is NOT imported (it rewrites a csv on import)',
   'resolve_attribution_domain' not in HELPER_SRC.replace('resolve_attribution_domain.py', '') and
   'import resolve_attribution_domain' not in SRC)

print('[b) roster / signature-block detector -- lane B detector D-P3b, reproduced]')
ok('thresholds are lane B\'s (>=10 distinct surnames, >=35% of tokens, >=12 tokens)',
   RF.ROSTER_MIN_DISTINCT == 10 and RF.ROSTER_MIN_RATIO == 0.35 and RF.ROSTER_MIN_TOKENS == 12)
ROLL = 'Alpher, Bravo, Charlie, Delta, Echo, Foxtrot, Golf, Hotel, India, Juliet, Kilo, and Lima.'
SUBSTANTIVE = ('Senator Bravo argued that the interchange amendment would gut the exemption '
               'and I think that is simply wrong for our credit unions.')
ok('a roll-call roster is a list artefact', RF.is_roster_list(ROLL, SUR))
ok('a substantive sentence naming one colleague is not', not RF.is_roster_list(SUBSTANTIVE, SUR))
# Negative control: the substantive fixture DOES carry a roster surname, so the False above is
# the thresholds doing work rather than a fixture with nothing to match.
ok('negative control: the substantive fixture trips a detector with no thresholds',
   RF.is_roster_list(SUBSTANTIVE, SUR, min_distinct=1, min_ratio=0.0))
_nine = ' '.join(['Alpher', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot', 'Golf', 'Hotel', 'India'] * 2)
ok('nine distinct surnames is below the floor', not RF.is_roster_list(_nine, SUR))
ok('the tenth distinct surname crosses it', RF.is_roster_list(_nine + ' Juliet Juliet', SUR))
_twelve = ' '.join(sorted(SUR))
ok('ratio boundary: 12 of 34 tokens (0.3529) is a list',
   RF.is_roster_list(_twelve + ' ' + ' '.join(['filler'] * 22), SUR))
ok('ratio boundary: 12 of 35 tokens (0.3429) is not',
   not RF.is_roster_list(_twelve + ' ' + ' '.join(['filler'] * 23), SUR))
ok('a short window is never a list', not RF.is_roster_list('Alpher Bravo Charlie Delta', SUR))
# The producer holds the whitespace-COLLAPSED window (member_reference_layer.py:132) while lane B
# measured on the raw slice.  The detector tokenises on non-letters, so the two agree; this is the
# property that carries lane B's graded precision onto the shipped call site.
_raw = ROLL.replace(', ', ',\n   ').replace(' and', '\tand')
ok('detector is whitespace-invariant (raw slice == collapsed window)',
   RF.is_roster_list(_raw, SUR) == RF.is_roster_list(RF.normalized_window(_raw), SUR) is True)
ok('surnames normalise like the producer (accents, apostrophes, case)',
   RF.roster_surnames(["Seán O'Donnell"]) == frozenset(['odonnell']) and
   RF.is_roster_list(' '.join(["O'DONNELL"] * 12 + ['filler'] * 10),
                     RF.roster_surnames(['Sean ODonnell'])) is False)

print('[c) identical windows count once -- per edge, whitespace-normalised]')
def _r(a, b, w, src='CREC'):
    return {'a': a, 'b': b, 'w': w, 'src': src}
_four = [_r('Ritchie Torres', 'Gregory Meeks', 'Today, @RepGregoryMeeks and I announced FHLBNY.', 'X')] * 4
kept, n = RF.collapse_identical(list(_four))
ok('four copies of one tweet contribute once (the measured Torres->Meeks edge)',
   len(kept) == 1 and n == 3)
kept, n = RF.collapse_identical([_r('A A', 'B B', 'one   two\nthree'), _r('A A', 'B B', 'one two three')])
ok('whitespace-only differences are the same window', len(kept) == 1 and n == 1)
kept, n = RF.collapse_identical([_r('A A', 'B B', 'same text'), _r('C C', 'B B', 'same text')])
ok('the same text on a DIFFERENT edge is kept (per edge, never global)', len(kept) == 2 and n == 0)
kept, n = RF.collapse_identical([_r('A A', 'B B', 'same text', 'CREC'), _r('A A', 'B B', 'same text', 'INTERVIEW')])
ok('identical windows collapse ACROSS sources on one edge (lane B keys on (a,b))',
   len(kept) == 1 and n == 1)
_ordered = [_r('A A', 'B B', 'first'), _r('A A', 'B B', 'first'), _r('A A', 'B B', 'second')]
kept, n = RF.collapse_identical(_ordered)
ok('the first copy is the one kept', [x['w'] for x in kept] == ['first', 'second'] and n == 1)
kept, n = RF.collapse_identical(_ordered, enabled=False)
ok('dial 0 is an exact identity (rollback = flip the dial)',
   kept == _ordered and [id(x) for x in kept] == [id(x) for x in _ordered] and n == 0)
_mixed = _four + _ordered
kept, n = RF.collapse_identical(list(_mixed))
ok('kept == distinct and excluded == surplus, for every edge at once',
   len(kept) == 3 and n == len(_mixed) - 3)

print('[the whole admission pass, as the producer calls it]')
REFS = [
    # PRESS: one own-domain headline, and one third-party article naming two colleagues
    _r('Seth Magaziner', 'Ben Bravo', 'Bravo and Magaziner on interchange', 'PRESS'),
    _r('Seth Magaziner', 'Cy Charlie', 'Charlie and Magaziner on interchange', 'PRESS'),
    _r('Seth Magaziner', 'Dee Delta', 'Delta praises Magaziner', 'PRESS'),
    # PRESS_OFFICIAL_MENTION is explicitly OUT of scope (0 violations measured)
    _r('Seth Magaziner', 'Eli Echo', 'Echo and Magaziner agree', 'PRESS_OFFICIAL_MENTION'),
    # a roll-call roster, any source
    _r('Ada Alpher', 'Ben Bravo', ROLL, 'CREC'),
    # ordinary substantive references, one of them duplicated
    _r('Ada Alpher', 'Cy Charlie', SUBSTANTIVE, 'CREC'),
    _r('Ada Alpher', 'Cy Charlie', SUBSTANTIVE, 'INTERVIEW'),
]
REFS[0]['url'] = 'https://www.americascreditunions.org/news/x'
REFS[1]['url'] = 'https://www.americascreditunions.org/news/x'
REFS[2]['url'] = 'https://magaziner.house.gov/media/y'
REFS[3]['url'] = 'https://www.americascreditunions.org/news/z'
_before = [dict(r) for r in REFS]
kept, excl = RF.apply_admission_filters(REFS, SUR)
ok('the third-party PRESS article loses both of its false edges',
   sorted((r['a'], r['b']) for r in kept if r['src'] == 'PRESS') == [('Seth Magaziner', 'Dee Delta')])
ok('the roll-call roster window is out', not any(r['w'] == ROLL for r in kept))
ok('PRESS_OFFICIAL_MENTION is untouched by the own-domain gate',
   sum(1 for r in kept if r['src'] == 'PRESS_OFFICIAL_MENTION') == 1)
ok('duplicates survive the admission pass -- filter (c) acts on the scored queue, not on n_raw',
   sum(1 for r in kept if r['w'] == SUBSTANTIVE) == 2)
ok('the admitted set is exactly the four survivors', len(kept) == 4, len(kept))
ok('the exclusion census names each filter and closes',
   dict(excl) == {'press_third_party_domain': 2, 'roster_list_window': 1}, dict(excl))
kept0, excl0 = RF.apply_admission_filters([dict(r) for r in _before], SUR,
                                          press_own_domain=False, roster_filter=False)
ok('both dials at 0 admit exactly the old set, in the old order',
   kept0 == _before and sum(excl0.values()) == 0)
kept1, excl1 = RF.apply_admission_filters([dict(r) for r in _before], SUR, roster_filter=False)
ok('the dials are independent: own-domain alone', len(kept1) == 5 and dict(excl1) == {'press_third_party_domain': 2})
kept2, excl2 = RF.apply_admission_filters([dict(r) for r in _before], SUR, press_own_domain=False)
ok('the dials are independent: roster alone', len(kept2) == 6 and dict(excl2) == {'roster_list_window': 1})

print('[the artifact block -- additive, and the accounting closes]')
BLK = RF.admission_filters_block({'REF_PRESS_OWN_DOMAIN': True, 'REF_ROSTER_FILTER': True,
                                  'REF_IDENTICAL_ONCE': False},
                                 {'press_third_party_domain': 8, 'roster_list_window': 749})
ok('dial states are reported as the strings the environment uses',
   BLK['dials'] == {'REF_PRESS_OWN_DOMAIN': '1', 'REF_ROSTER_FILTER': '1', 'REF_IDENTICAL_ONCE': '0'})
ok('every dial default is the new behaviour', BLK['defaults'] == dict.fromkeys(RF.DIALS, '1'))
ok('every filter has a count, present or zero',
   set(BLK['excluded']) == set(RF.EXCLUSION_KEYS) and BLK['excluded']['identical_window_repeat'] == 0)
ok('the total closes against the parts', BLK['excluded_total'] == 757 == sum(BLK['excluded'].values()))
ok('the block is json-serialisable and schema-stamped',
   __import__('json').dumps(BLK, sort_keys=True) and
   BLK['schema'] == 'member-reference-admission-filters-v1')
ok('every filter carries its rule in one line', set(BLK['rules']) == set(RF.EXCLUSION_KEYS))

print('[the helper is pure: no data, no environment, no side effects]')
for _bad in ('open(', 'json.load', 'sqlite3', 'from paths', 'import paths', 'dump_atomic',
             'os.environ', 'moneyball_core', 'press_stream'):
    ok('helper contains no %r' % _bad, _bad not in HELPER_SRC)
ok('helper imports only the stdlib pieces it needs',
   re.findall(r'^import .*$|^from .* import .*$', HELPER_SRC, re.M) == ['import collections, re, unicodedata'],
   re.findall(r'^import .*$|^from .* import .*$', HELPER_SRC, re.M))

print('[the wiring in member_reference_layer.py, read as text -- never imported]')
ok('the producer imports the helper',
   'from members import reference_filters as RF' in SRC)
for _dial in RF.DIALS:
    ok('%s defaults to the new behaviour' % _dial,
       "%s = os.environ.get('%s', '1') == '1'" % (_dial, _dial) in SRC)
    ok('%s documents that 0 restores the old behaviour' % _dial,
       re.search(r"^%s = .*old behaviour\)" % _dial, SRC, re.M) is not None)
ok('the PRESS pass carries the row url the gate needs', "'url': r.get('url') or ''" in SRC)
ok('admission filters run inside extract(), before any queue is built',
   'refs, _excl = RF.apply_admission_filters(refs, frozenset(SURNAMES),' in SRC and
   SRC.index('RF.apply_admission_filters') < SRC.index('bykey = collections.defaultdict(list)'))
ok('the exclusion census is accumulated', 'FILTERED.update(_excl)' in SRC and
   'FILTERED = collections.Counter()' in SRC)
ok('the paid target set is collapsed, so --score never pays for a repeat',
   'target_all, _dup_target = RF.collapse_identical(target_all, REF_IDENTICAL_ONCE)' in SRC)
ok('the scored set is collapsed, so n_scored and the tallies count a window once',
   'todo_all, _dup_scored = RF.collapse_identical(todo_all, REF_IDENTICAL_ONCE)' in SRC and
   "FILTERED['identical_window_repeat'] = _dup_scored" in SRC)
ok('the artifact gains one additive block', "'admission_filters': RF.admission_filters_block(" in SRC)
ok('the dial state is built once and shared by the probe and the artifact',
   "AF_STATE = {'REF_PRESS_OWN_DOMAIN': REF_PRESS_OWN_DOMAIN," in SRC and
   SRC.count('RF.admission_filters_block(AF_STATE, FILTERED)') == 2)
ok('the free probe reports what it excluded, and says what it cannot yet count',
   "print('  admission filters:" in SRC and
   'identical_window_repeat is counted where the scored set is built' in SRC)
# Nothing in the paid path changed except which windows reach it.
ok('the classifier contract is untouched',
   SRC.count("output_config={\"format\": {\"type\": \"json_schema\", \"schema\": SCHEMA}}") == 1 and
   "DYAD_CAP = int(os.environ.get('REF_DYAD_CAP', '4'))" in SRC and
   "REF_WORKERS" in SRC)
ok('no stance/category/direction label is touched anywhere in the change',
   all(_w not in HELPER_SRC for _w in ('Champion', 'Ally', 'Skeptic', 'positive', 'negative',
                                       'neutral-substantive', 'formality')))

print('REFERENCE ADMISSION FILTER TESTS — %d passed, %d FAILED' % (P, F))
sys.exit(1 if F else 0)
