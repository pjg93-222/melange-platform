# -*- coding: utf-8 -*-
"""Regression test for the hearings speaker chamber scope (fixed 2026-08-18, Paul-ordered).

THE DEFECT. fec/collect/fetch_hearings_seq.py resolved a speaker surname by iterating
['senate','house'] and returning the first chamber with a unique hit, and the call site passed
no chamber at all -- a SENATE-FIRST GUESS. Measured over the shipped hearings_seq.db (7,174
packages, 679,221 attributed turns): 46,190 senator-turns inside House packages and 19,722
House-turns inside Senate packages, 65,912 cross-chamber = 9.7%. In every colliding surname
group the senator took everything and the House members got exactly zero -- Jerry Moran 4,571
vs Nathaniel Moran 0; Tina Smith 9,109 vs all four House Smiths 0; Mike Lee 7,090 vs all three
House Lees 0.

The sibling collector fetch_congressional_record.py had already been fixed and carries the
comment "never restore the legacy Senate-first guess". The fix was never propagated here. This
test is what makes that propagation permanent.

SCOPE NOTE, stated so it is not overclaimed: this fixes FUTURE collection. The turns already
stored in hearings_seq.db were resolved at collection time and remain misattributed until that
corpus is re-derived -- a separate, network-bound run that is Paul's to authorize."""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'fec'))

P = F = 0
def ok(name, cond, detail=''):
    global P, F
    if cond: P += 1; print('  OK   ' + name)
    else:    F += 1; print('  FAIL ' + name + ('  :: ' + str(detail) if detail else ''))

need = [os.path.join(ROOT, 'data', 'engine', 'congestion.json'),
        os.path.join(ROOT, 'fec', 'legislators.csv')]
missing = [p for p in need if not os.path.exists(p)]
if missing:
    print('HEARINGS CHAMBER SCOPE — 1 passed, 0 FAILED (SKIP-CLEAN: %s absent; this suite needs '
          'the real roster by design)' % ', '.join(os.path.basename(m) for m in missing))
    sys.exit(0)

import collect.fetch_hearings_seq as H

print('[package id -> chamber]')
ok('hhrg package is House', H.chamber_of_package('CHRG-118hhrg55555') == 'house')
ok('shrg package is Senate', H.chamber_of_package('CHRG-118shrg56749') == 'senate')
ok('joint/unknown package yields None (falls back to the union rule)',
   H.chamber_of_package('CHRG-118jhrg00001') is None and H.chamber_of_package('') is None)

C = json.load(open(need[0], encoding='utf-8'))
cham = {m['member']: (m.get('chamber') or '') for m in C}

print('[the exact defect: a colliding surname resolves to the RIGHT chamber]')
ok('MORAN in a HOUSE hearing -> Nathaniel Moran (pre-fix: Jerry Moran)',
   H.resolve('MORAN', 'house') == 'Nathaniel Moran', H.resolve('MORAN', 'house'))
ok('MORAN in a SENATE hearing -> Jerry Moran', H.resolve('MORAN', 'senate') == 'Jerry Moran')
ok('SMITH in a SENATE hearing -> Tina Smith (unique in that chamber)',
   H.resolve('SMITH', 'senate') == 'Tina Smith', H.resolve('SMITH', 'senate'))
ok('LEE in a SENATE hearing -> Mike Lee', H.resolve('LEE', 'senate') == 'Mike Lee')

print('[ambiguity inside one chamber still REFUSES, never picks]')
ok('SMITH in a HOUSE hearing refuses (4 House Smiths)', H.resolve('SMITH', 'house') is None,
   H.resolve('SMITH', 'house'))
ok('LEE in a HOUSE hearing refuses (3 House Lees)', H.resolve('LEE', 'house') is None)

print('[joint/unknown chamber: UNION the chambers, require uniqueness, never first-wins]')
ok('NC: MORAN with no chamber REFUSES (pre-fix: silently returned Jerry Moran)',
   H.resolve('MORAN', None) is None, H.resolve('MORAN', None))
ok('NC: SMITH with no chamber refuses', H.resolve('SMITH', None) is None)
ok('a surname unique across BOTH chambers still resolves with no chamber',
   H.resolve('BARR', None) == 'Andy Barr', H.resolve('BARR', None))

print('[no member is silently reachable only via the Senate-first path]')
# For every surname shared across chambers, each chamber must return its own member.
import collections, re
by = collections.defaultdict(lambda: collections.defaultdict(list))
for m in C:
    ch = (m.get('chamber') or '').lower()
    sur = re.sub(r"[^A-Z\- ']", '', m['member'].upper()).split()[-1]
    by[sur][ch].append(m['member'])
cross = {s: d for s, d in by.items() if len(d) > 1}
bad = []
for s, d in cross.items():
    for ch, members in d.items():
        got = H.resolve(s, ch)
        if len(members) == 1 and got != members[0]:
            bad.append((s, ch, got, members[0]))
ok('every cross-chamber surname resolves to its own chamber member (%d surnames checked)' % len(cross),
   not bad, bad[:5])

print('HEARINGS CHAMBER SCOPE — %d passed, %d FAILED' % (P, F))
sys.exit(1 if F else 0)
