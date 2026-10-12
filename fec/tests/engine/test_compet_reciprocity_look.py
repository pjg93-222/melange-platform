# -*- coding: utf-8 -*-
"""CM-1 look gate (compet_reciprocity_prereg.md). FIXTURE ONLY: gate() is pure, so no prereg, artifact or live
file is read; nothing is written. The look refuses without LOCKED, with a RESULT already present, on fingerprint drift,
before 2028-02-15, without a COLLECTION RECORD, and on a collection artifact whose sha differs from that record."""
from __future__ import annotations

import datetime as dt
import os
import sys

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)
from engine import compet_reciprocity_look as CR  # noqa: E402  import-safe: nothing runs on import

GOOD_FP = {'c': CR.PIN_C}
OUT_SHA, MON_SHA = 'a' * 64, 'b' * 64
CODE_LINES = ''.join('- `%s`: `%s`\n' % (rel, sha) for rel, sha in CR.PIN_CODE.items())
RECORD = ('\n## MONEY RECORD\n\nmoney_sha256: `%s`\n' % MON_SHA
          + '\n## COLLECTION RECORD\n\noutcome_sha256: `%s`\nmoney_sha256: `%s`\n' % (OUT_SHA, MON_SHA) + CODE_LINES)
LOCKED = '# CM-1\n\nSTATUS: LOCKED (fixture)\n\nThe look is gated on the absence of a line-leading ## RESULT-R heading.\n'
DAY = dt.date(2028, 2, 15)
SHA = {CR.OUTCOME: OUT_SHA, CR.MONEY: MON_SHA}.get


def run(text, fp=GOOD_FP, today=DAY, sha=SHA, exists=lambda p: True):
    return CR.gate(text, fp, today, sha=sha, exists=exists)


def test_open_when_every_condition_holds():
    assert run(LOCKED + RECORD) is None


def test_refuses_absent_and_unlocked():
    assert 'ABSENT' in run('')
    assert 'not LOCKED' in run(LOCKED.replace('STATUS: LOCKED', 'STATUS: DRAFT') + RECORD)
    prose = LOCKED.replace('STATUS: LOCKED', 'STATUS: DRAFT') + 'Refused while `STATUS: LOCKED` is absent.\n'
    assert 'not LOCKED' in run(prose + RECORD)          # a mid-line quote of the lock line does not lock


def test_refuses_when_result_present_and_prose_does_not_trip():
    assert 'already recorded' in run(LOCKED + RECORD + '\n## RESULT-R\n\nTIER T3\n')
    assert '## RESULT-R' in LOCKED and run(LOCKED + RECORD) is None   # mid-line prose quote is not a heading


def test_refuses_fingerprint_drift():
    assert 'fingerprint drift' in run(LOCKED + RECORD, fp={'c': '0' * 64})
    assert 'fingerprint drift' in run(LOCKED + RECORD, fp={'c': None})


def test_refuses_record_not_attesting_pinned_classification_code():
    for rel, sha in CR.PIN_CODE.items():
        drifted = RECORD.replace('- `%s`: `%s`\n' % (rel, sha), '- `%s`: `%s`\n' % (rel, '0' * 64))
        assert 'classification code' in run(LOCKED + drifted), rel


def test_refuses_before_look_date():
    assert 'look date' in run(LOCKED + RECORD, today=dt.date(2028, 2, 14))


def test_refuses_missing_or_mismatched_collection():
    assert 'COLLECTION RECORD' in run(LOCKED)
    assert 'COLLECTION RECORD' in run(LOCKED + '\n## COLLECTION RECORD\n\noutcome_sha256: `%s`\n' % OUT_SHA)
    only_coll = RECORD.split('\n## COLLECTION RECORD')[1]
    assert 'COLLECTION RECORD' in run(LOCKED + '\n## COLLECTION RECORD' + only_coll)          # no MONEY RECORD
    assert 'COLLECTION RECORD' in run(LOCKED + RECORD.replace('money_sha256: `%s`\n\n' % MON_SHA,
                                                             'money_sha256: `%s`\n\n' % ('d' * 64), 1))  # pins disagree
    assert 'artifact absent' in run(LOCKED + RECORD, exists=lambda p: p != CR.MONEY)
    assert 'differs' in run(LOCKED + RECORD, sha={CR.OUTCOME: OUT_SHA, CR.MONEY: 'c' * 64}.get)


def test_tier_partition_is_total_and_ordered():
    """T1 needs r>=.10, p<.05 and a positive beta in both chambers; T4 needs r<=-.10 and p<.05 negative."""
    import numpy as np
    rng = np.random.default_rng(7)
    n = 400
    X = CR.design(1 + rng.random(n), rng.random(n) * 8, rng.random(n), rng.integers(0, 2, n), np.r_[np.zeros(300), np.ones(100)])
    lat = 0.8 * CR._z(CR._resid(X[:, 3], X[:, [0, 1, 2, 4, 5, 6]])) + rng.standard_normal(n)
    t, f = CR.tier(CR._censor(lat, 0.5), X, CR.COLS)
    assert t == 'T1' and f['r'] >= 0.10 and f['beta_house'] > 0 and f['beta_senate'] > 0
    t, f = CR.tier(CR._censor(-lat, 0.5), X, CR.COLS)
    assert t == 'T4' and f['r'] <= -0.10
