# -*- coding: utf-8 -*-
"""Kernel suite for fec/audit/audit_rendered_desk.py -- the rendered-desk audit.

Two halves:

  1. the audit run against the CURRENT shipped artifacts, which must be clean (both DESK_SPLIT
     defects are fixed: e02c44527 for the bill repository, dbf9e61a0 for the staffer index);

  2. NEGATIVE CONTROLS.  A check that cannot fail is worse than none -- the bundle audit stayed
     green through both outages precisely because nothing it asserted could go red on this defect
     class.  Each control re-introduces one real defect into a COPY of the artifact in a temp dir
     and asserts that the specific assertion meant to catch it goes red, for the right reason.

  C1  drop rebuildBillRepository() from the merge hook   -> A2, A4, A5b, A5c fail  (e02c44527)
  C2  drop rebuildStaffIndex() from the merge hook       -> A3, A4, A5b, A5c fail  (dbf9e61a0)
  C3  add a module-scope index over a deferred field     -> A5b, A5c fail   (a NEW instance)
  C6  make the ranked BOARD read a deferred field        -> A5d fails      (no count would show it)
  C4  feed a bundle with no bills and no staffdir        -> CONTRACT fails, does not pass on nothing
  C5a feed an unsplit (DESK_SPLIT=0) artifact            -> A1 skips with a reason, never passes
  C5b feed an artifact that does not actually defer      -> A1 skips with a reason, never passes

C1/C2 each trip four independent detectors and C3/C6 trip disjoint ones, which is the point: no
single assertion is carrying the whole class, and the two that catch a NEW defect (A5b, A5c, A5d)
need nobody to add anything to a list.

The suite SKIPs cleanly -- one visible pass, zero failures -- when the shipped artifacts or node
are absent, so a fresh clone with no built surfaces still goes green.
"""
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_FEC = os.path.dirname(os.path.dirname(_HERE))
if _FEC not in sys.path:
    sys.path.insert(0, _FEC)

from audit import audit_rendered_desk as ARD           # noqa: E402
from paths import p                                    # noqa: E402

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('  OK   ' if cond else 'FAIL  ') + name + (('  :: ' + str(detail)) if (detail and not cond) else ''))


def verdict():
    print('\nrendered desk: %d passed, %d failed' % (P, F))
    return 1 if F else 0


def status_of(res, prefix):
    """(status, detail) of the first check whose name starts with prefix."""
    for status, name, detail in res.checks:
        if name.startswith(prefix):
            return status, detail
    return None, ''


HTML = p('cu-member-desk.html')
PROFILES = p('cu-member-desk-profiles.js')
BUNDLE = p('member_desk_bundle.json')
NODE = os.environ.get('RENDERED_DESK_NODE') or shutil.which('node')

_missing = [q for q in (HTML, PROFILES, BUNDLE, ARD.PROBE) if not os.path.exists(q)]
if _missing or not NODE:
    reason = ('node not found' if not _missing else
              'shipped artifacts absent: ' + ', '.join(os.path.basename(q) for q in _missing))
    ok('rendered-desk suite SKIP-CLEAN (%s)' % reason, True)
    # The audit itself must also degrade to SKIP rather than raising, on this same machine state.
    res = ARD.audit()
    ok('audit degrades to SKIP, not FAIL, without artifacts or node',
       res.failed == 0 and res.skipped > 0, [c[:2] for c in res.checks])
    sys.exit(verdict())


def mutate(dst_name, transform, tmp):
    """Write a mutated COPY of the shipped page. Never touches the live artifact."""
    with open(HTML, encoding='utf-8', newline='') as fh:
        src = fh.read()
    new = transform(src)
    assert new != src, 'control mutation for %s changed nothing -- the anchor text moved' % dst_name
    dst = os.path.join(tmp, dst_name)
    with open(dst, 'w', encoding='utf-8', newline='') as fh:
        fh.write(new)
    return dst


def replace_once(src, token, replacement, label):
    n = src.count(token)
    assert n == 1, 'control anchor %s occurs %d times, expected exactly 1' % (label, n)
    return src.replace(token, replacement)


BYNAME = '\nconst byName={}; M.forEach((m,i)=>byName[m.name]=i);'

print('[rendered desk: live artifacts]')
live = ARD.audit()
ok('audit runs clean against the shipped artifacts',
   live.failed == 0 and live.passed >= 12,
   'passed=%d failed=%d skipped=%d :: %s' % (live.passed, live.failed, live.skipped,
                                             [(s, n) for s, n, _ in live.checks if s != 'PASS']))
for _prefix in ('A1', 'A2 ', 'A3 ', 'A4 ', 'A5b', 'A5c', 'A5d', 'A6'):
    _s, _d = status_of(live, _prefix)
    ok('%s passes on the shipped artifacts' % _prefix.strip(), _s == 'PASS', '%s :: %s' % (_s, _d))
_exp = live.info.get('expected') or {}
ok('expectations were derived from the bundle, not from the page',
   _exp.get('source', '').endswith('member_desk_bundle.json') and _exp.get('bill_keys', 0) > 0
   and _exp.get('staff_ids', 0) > 0,
   _exp)

print('\n[rendered desk: negative controls]')
_tmp = tempfile.mkdtemp(prefix='rendered_desk_controls_')
try:
    # ---- C1: the bill-repository rebuild is dropped from the merge hook (the e02c44527 defect).
    # The module-scope call at column 0 stays, so BILLS is still built once -- over the deferred
    # field before it arrives, which is exactly how it froze empty in the shipped page.
    c1 = mutate('c1_no_bill_rebuild.html',
                lambda s: replace_once(s, '\n  rebuildBillRepository();', '\n  ;', 'hook bill rebuild'),
                _tmp)
    r1 = ARD.audit(html=c1, profiles=PROFILES, bundle=BUNDLE)
    s1, d1 = status_of(r1, 'A2 ')
    ok('C1 dropping rebuildBillRepository fails A2', s1 == 'FAIL', '%s :: %s' % (s1, d1))
    ok('C1 A2 failure names both sources', 'expected-source' in d1 and 'observed-source' in d1
       and 'observed BILLS=0' in d1, d1)
    # Three independent detectors must see the same defect: the count, the observed call list, and
    # the deferred-field read set. If only one ever fires, the other two are decoration.
    s1b, d1b = status_of(r1, 'A5b')
    ok('C1 also fails A5b naming the field that stopped being rebuilt',
       s1b == 'FAIL' and "'bills'" in d1b.split('NEVER REBUILT:')[-1], d1b)
    ok('C1 also fails A4 on the observed call list',
       status_of(r1, 'A4 ')[0] == 'FAIL'
       and 'rebuildBillRepository' not in status_of(r1, 'A4 ')[1].split('call order=')[-1],
       status_of(r1, 'A4 ')[1])

    # ---- C2: the staff-index rebuild is dropped from the merge hook (the dbf9e61a0 defect).
    c2 = mutate('c2_no_staff_rebuild.html',
                lambda s: replace_once(s, '\n  rebuildStaffIndex();', '\n  ;', 'hook staff rebuild'),
                _tmp)
    r2 = ARD.audit(html=c2, profiles=PROFILES, bundle=BUNDLE)
    s2, d2 = status_of(r2, 'A3 ')
    ok('C2 dropping rebuildStaffIndex fails A3', s2 == 'FAIL', '%s :: %s' % (s2, d2))
    ok('C2 A3 failure names both sources', 'expected-source' in d2 and 'observed-source' in d2
       and 'observed STFIDX=0' in d2, d2)
    s2b, d2b = status_of(r2, 'A5b')
    ok('C2 also fails A5b naming the field that stopped being rebuilt',
       s2b == 'FAIL' and "'staffdir'" in d2b.split('NEVER REBUILT:')[-1], d2b)

    # ---- C3: a BRAND NEW module-scope index over a deferred field (psy), with no rebuild.  This is
    # the control that decides whether the class is closed or merely patched twice: nobody adds
    # PSYIDX to an allowlist, and the audit must still catch it.
    c3 = mutate('c3_new_deferred_index.html',
                lambda s: replace_once(
                    s, BYNAME,
                    BYNAME + '\nconst PSYIDX={};M.forEach((m,i)=>{if(m.psy)PSYIDX[i]=1;});',
                    'byName anchor'),
                _tmp)
    r3 = ARD.audit(html=c3, profiles=PROFILES, bundle=BUNDLE)
    s3b, d3b = status_of(r3, 'A5b')
    s3c, d3c = status_of(r3, 'A5c')
    ok('C3 a new unrebuilt index over a deferred field fails A5',
       s3b == 'FAIL' or s3c == 'FAIL', 'A5b=%s A5c=%s' % (s3b, s3c))
    ok('C3 A5b names the deferred field that is never rebuilt',
       s3b == 'FAIL' and "'psy'" in d3b.split('NEVER REBUILT:')[-1], d3b)
    ok('C3 A5c names the stale binding',
       s3c == 'FAIL' and 'PSYIDX' in d3c.split('STALE AFTER MERGE:')[-1], d3c)
    ok('C3 leaves A2 and A3 green -- the guard fired on the new index, not on collateral damage',
       status_of(r3, 'A2 ')[0] == 'PASS' and status_of(r3, 'A3 ')[0] == 'PASS',
       '%s / %s' % (status_of(r3, 'A2 ')[0], status_of(r3, 'A3 ')[0]))

    # ---- C6: make the RANKED BOARD itself depend on a deferred field.  Nothing about the indexes
    # changes and no new binding appears, so A2/A3/A5c all stay green; only the rendered markup
    # differs from the monolith's.  This is the control for the half of the defect class that has
    # no count attached to it -- the board renders once at module scope and is never re-rendered.
    RANK_ANCHOR = '\n  if(area&&rstate.sort!==\'tier\'){r.sort('
    c6 = mutate('c6_board_reads_deferred.html',
                lambda s: replace_once(
                    s, RANK_ANCHOR,
                    '\n  r=r.filter((m,ix)=>!(m.psy&&ix%2===1));' + RANK_ANCHOR, 'renderRanked sort'),
                _tmp)
    r6 = ARD.audit(html=c6, profiles=PROFILES, bundle=BUNDLE)
    s6, d6 = status_of(r6, 'A5d')
    ok('C6 a board that reads a deferred field fails A5d', s6 == 'FAIL', '%s :: %s' % (s6, d6))
    ok('C6 A5d compares a non-empty board, not two blanks',
       s6 == 'FAIL' and ' 0 bytes' not in d6, d6)
    ok('C6 leaves the index assertions green -- only the rendered markup is wrong',
       status_of(r6, 'A2 ')[0] == 'PASS' and status_of(r6, 'A3 ')[0] == 'PASS'
       and status_of(r6, 'A5c')[0] == 'PASS',
       [(s, n) for s, n, _ in r6.checks if s != 'PASS'])

    # ---- C4: a degenerate bundle must fail the contract, never sail through as 0-expected/0-seen.
    c4_bundle = os.path.join(_tmp, 'c4_empty_bundle.json')
    with open(c4_bundle, 'w', encoding='utf-8') as fh:
        fh.write('{"members":[{"name":"Test Member","bills":[],"staffdir":[]}]}')
    r4 = ARD.audit(html=HTML, profiles=PROFILES, bundle=c4_bundle)
    s4, d4 = status_of(r4, 'CONTRACT')
    ok('C4 a bundle with no bills and no staffdir fails the contract', s4 == 'FAIL', '%s :: %s' % (s4, d4))
    ok('C4 no assertion is reported as passing on the degenerate bundle',
       not any(s == 'PASS' and n.startswith('A') for s, n, _ in r4.checks),
       [(s, n) for s, n, _ in r4.checks])

    # ---- C5a: an unsplit (DESK_SPLIT=0) artifact.  Every deferred-field assertion is vacuous
    # against it, so A1 must SKIP with a reason rather than report a pass it did not earn.
    c5 = mutate('c5_monolith.html',
                lambda s: replace_once(s, '\nconst DSPLIT=1;', '\nconst DSPLIT=0;', 'DSPLIT'),
                _tmp)
    r5 = ARD.audit(html=c5, profiles=PROFILES, bundle=BUNDLE)
    s5, d5 = status_of(r5, 'A1')
    ok('C5a an unsplit artifact makes A1 skip, not pass', s5 == 'SKIP', '%s :: %s' % (s5, d5))
    ok('C5a the skip carries a reason', 'not split' in d5 and 'DSPLIT' in d5, d5)
    ok('C5a nothing downstream of A1 reports a pass',
       not any(s == 'PASS' and n.startswith('A') for s, n, _ in r5.checks),
       [(s, n) for s, n, _ in r5.checks])

    # ---- C5b: the other way an artifact can stop deferring -- DSPLIT is still 1 but the fields are
    # present at parse time, so the "empty before the merge" canary is meaningless.  Also a skip.
    c5b = mutate('c5b_not_deferring.html',
                 lambda s: replace_once(
                     s, BYNAME,
                     BYNAME + "\nM[0].bills=[{bill:'H.R.1',congress:119,role:'sponsor'}];"
                              "M[0].staffdir=[{id:'probe-staff-1'}];",
                     'byName anchor'),
                 _tmp)
    r5b = ARD.audit(html=c5b, profiles=PROFILES, bundle=BUNDLE)
    s5b, d5b = status_of(r5b, 'A1')
    ok('C5b an artifact that does not actually defer makes A1 skip, not pass', s5b == 'SKIP',
       '%s :: %s' % (s5b, d5b))
    ok('C5b the skip names the structures that were already populated',
       'already populated before the merge' in d5b, d5b)
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

sys.exit(verdict())
