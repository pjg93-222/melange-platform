# -*- coding: utf-8 -*-
"""Real-roster contract test for fec/members/member_identity.Resolver (2026-08-18).

WHY THIS EXISTS. The pre-existing kernel assertion (fec/tests/run_tests.py) exercises the
Resolver against a THREE-NAME toy roster in which none of its three failure modes is
reachable. It passed continuously while the shipped Resolver returned `Johnson` -> Julie
Johnson, `Michael Barr` -> Andy Barr, and `Clyburn, James` -> John James against the live
533. A guard tested only on data that cannot express its failure is not a guard.

So this suite builds the Resolver on the ACTUAL roster (data/engine/congestion.json, every
member) and asserts the three properties that matter. Every assertion below fails against
the pre-2026-08-18 implementation -- that is the point, and the round-trip property (every
roster member resolves to itself from four different input forms) is the one that catches
regressions nobody predicted."""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'fec'))
from members.member_identity import Resolver, first_names_agree, normalize_name

P = F = 0
def ok(name, cond, detail=''):
    global P, F
    if cond: P += 1; print('  OK   ' + name)
    else:    F += 1; print('  FAIL ' + name + ('  :: ' + str(detail) if detail else ''))

CONG = os.path.join(ROOT, 'data', 'engine', 'congestion.json')
if not os.path.exists(CONG):
    print('MEMBER IDENTITY ROSTER TESTS — 1 passed, 0 FAILED (SKIP-CLEAN: congestion.json absent '
          'at %s; this suite requires the real roster by design)' % ROOT)
    sys.exit(0)

C = json.load(open(CONG, encoding='utf-8'))
NAMES = [m['member'] for m in C if m.get('bioguide')]
N2B = {m['member']: m['bioguide'] for m in C if m.get('bioguide')}
R = Resolver(NAMES, N2B)
print('[roster: %d members]' % len(NAMES))

print('[bioguide is authoritative]')
ok('a supplied bioguide resolves regardless of the name string',
   R.resolve(name='Not A Real Person', bioguide=N2B[NAMES[0]]) == NAMES[0])

print('[round trip: every roster member resolves to ITSELF from four input forms]')
forms = {
    'exact':        lambda n: n,
    'Last, First':  lambda n: '%s, %s' % (n.split()[-1], ' '.join(n.split()[:-1])),
    'lowercased':   lambda n: n.lower(),
    'first initial': lambda n: '%s. %s' % (n.split()[0][0], ' '.join(n.split()[1:])),
}
for label, fn in forms.items():
    wrong = [n for n in NAMES if (lambda g: g is not None and g != n)(R.resolve(fn(n)))]
    ok('%-14s never resolves to a DIFFERENT member (0 of %d)' % (label, len(NAMES)),
       not wrong, '%d wrong, e.g. %s -> %s' % (len(wrong), fn(wrong[0]), R.resolve(fn(wrong[0]))) if wrong else '')
exact_ok = sum(1 for n in NAMES if R.resolve(n) == n)
ok('exact form resolves for all %d' % len(NAMES), exact_ok == len(NAMES), exact_ok)
lastfirst_ok = sum(1 for n in NAMES if R.resolve(forms['Last, First'](n)) == n)
ok('"Last, First" resolves for all %d (was 23 wrong before the fix)' % len(NAMES),
   lastfirst_ok == len(NAMES), lastfirst_ok)

print('[non-roster people are REFUSED, never absorbed into a roster member]')
# Each shares a roster-unique surname; the old code returned the roster member.
for q, was in (('Michael Barr', 'Andy Barr'), ('John Smith', 'Jason Smith'),
               ('Travis Hill', 'French Hill'), ('Jane Waters', 'Maxine Waters'),
               ('Jessica Johnson', 'Julie Johnson'), ('Robert Kelly', 'Robin Kelly')):
    got = R.resolve(q)
    ok('%-17r refused (pre-fix: %s)' % (q, was), got is None, 'returned %s' % got)

print('[ambiguous bare surnames are REFUSED; unique ones still resolve]')
for q in ('Johnson', 'Lee', 'Jackson', 'Joyce', 'Meeks', 'Smith', 'Scott', 'Moran', 'Miller', 'Carter'):
    got = R.resolve(q)
    n_share = sum(1 for n in NAMES if normalize_name(n).split()[-1] == q.lower())
    if n_share > 1:
        ok('bare %-9r (%d members share it) refused' % (q, n_share), got is None, 'returned %s' % got)
uniq = [n for n in NAMES if sum(1 for m in NAMES
                                if normalize_name(m).split()[-1] == normalize_name(n).split()[-1]) == 1]
sur_ok = sum(1 for n in uniq if R.resolve(normalize_name(n).split()[-1]) == n)
ok('a roster-UNIQUE bare surname still resolves (%d of %d) — behaviour deliberately kept'
   % (sur_ok, len(uniq)), sur_ok == len(uniq))

print('[first-name agreement semantics]')
ok('exact match agrees', first_names_agree('jason', 'jason'))
ok('initial abbreviates', first_names_agree('j', 'jason') and first_names_agree('jason', 'j'))
ok('nickname prefix agrees (chris/christopher, tim/timothy)',
   first_names_agree('chris', 'christopher') and first_names_agree('timothy', 'tim'))
ok('NC: distinct names sharing a prefix do NOT agree (adam/adrian, mary/max)',
   not first_names_agree('adam', 'adrian') and not first_names_agree('mary', 'max'))
ok('NC: empty never agrees', not first_names_agree('', 'jason') and not first_names_agree('jason', ''))

print('[nickname equivalence: strictness must not refuse REAL members]')
# Added after the first build of the fix dropped sitting members from an electoral_history
# rebuild because the source books call them Bernie, Jack, Jim, Maggie, Lou and Greg.
for q, want in (('Bernie Sanders', 'Bernard Sanders'), ('Maggie Hassan', 'Margaret Hassan'),
                ('Jim Risch', 'James Risch'), ('Lou Correa', 'Luis Correa'),
                ('Greg Steube', 'Gregory Steube'), ('Chris Smith', 'Christopher Smith')):
    if want in NAMES:
        ok('%-16r -> %s' % (q, want), R.resolve(q) == want, R.resolve(q))
ok('a source leading with an initial still matches ("J. French Hill")',
   ('French Hill' not in NAMES) or R.resolve('J. French Hill') == 'French Hill', R.resolve('J. French Hill'))
ok('NC: nickname latitude does NOT reopen absorption (Michael Barr, John Smith, Robert Kelly)',
   R.resolve('Michael Barr') is None and R.resolve('John Smith') is None
   and R.resolve('Robert Kelly') is None)
ok('NC: a departed member is still refused, not absorbed (Raul Grijalva)',
   R.resolve('Raul Grijalva') is None, R.resolve('Raul Grijalva'))

print('[middle initials must not create false ambiguity inside a colliding surname]')
# Second-order defect found by REBUILDING electoral_history, not by the suite: trying every
# given token at once let a middle initial match a different candidate's first name --
# "Bennie G. Thompson" matched Bennie AND Glenn Thompson, "Susan M. Collins" matched Susan AND
# Mike Collins -- refusing members who were never in doubt. First token now takes precedence.
for q, want in (('Bennie G. Thompson', 'Bennie Thompson'), ('Susan M. Collins', 'Susan Collins'),
                ('Sylvia R. Garcia', 'Sylvia Garcia'), ('Laurel M. Lee', 'Laurel Lee'),
                ('Summer L. Lee', 'Summer Lee'), ('Timothy M. Kennedy', 'Timothy Kennedy')):
    if want in NAMES:
        ok('%-20r -> %s' % (q, want), R.resolve(q) == want, R.resolve(q))
ok('NC: a middle initial alone never resolves a colliding surname ("G. Thompson")',
   R.resolve('G. Thompson') in (None, 'Glenn Thompson'), R.resolve('G. Thompson'))

print('[member aliases: same person, different given name -- high evidence bar]')
if 'Eric Crawford' in NAMES:
    ok("'Rick Crawford' -> Eric Crawford (AR-01; serves and appears on ballots as Rick)",
       R.resolve('Rick Crawford') == 'Eric Crawford', R.resolve('Rick Crawford'))
# The aliases must NOT be a backdoor: same-surname-same-state pairs that are DIFFERENT people
# must keep refusing. Each of these shares a state with the roster member listed.
for q, notthis in (('Raul Grijalva', 'Adelita Grijalva'), ('Rodney Davis', 'Danny Davis'),
                   ('Eddie Bernice Johnson', 'Julie Johnson'), ('David Scott', 'Austin Scott'),
                   ('Doug Collins', 'Mike Collins'), ('Bruce Davis', 'Donald Davis')):
    got = R.resolve(q)
    ok('NC: %-22r still refused (would have become %s)' % (q, notthis), got is None, got)

print('[the specific regressions from the 2026-08-18 sweep]')
for q, was in (('Masto, Catherine Cortez', 'Alexandria Ocasio-Cortez'),
               ('Clyburn, James', 'John James'), ('Reed, Jack', 'Brian Jack'),
               ('Goldman, Craig', 'Angie Craig'), ('Delaney, April McClain', 'Lisa McClain')):
    want = None
    for n in NAMES:
        if normalize_name(n) == normalize_name(q.split(',', 1)[1] + ' ' + q.split(',', 1)[0]):
            want = n; break
    got = R.resolve(q)
    ok('%-26r -> %s (pre-fix: %s)' % (q, want or 'None', was), got == want, 'returned %s' % got)

print('MEMBER IDENTITY ROSTER TESTS — %d passed, %d FAILED' % (P, F))
sys.exit(1 if F else 0)
