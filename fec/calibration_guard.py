# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); from paths import ROOT as MB_ROOT
"""CALIBRATION GUARD — halt when a chain run would silently REVERT a ratified calibration.

WHY THIS EXISTS (incident, 2026-07-19). Paul ratified a stance-ladder tightening on lane
claude/legislative-letters-import-be42f9 @f6388d9: OVERALL_BANDS Ally 3.00, opponent floor -1.50,
scale 'strict-fixed...v6'. The live tree sat on a branch that never received it. A regeneration then
rewrote the PUBLISHED ladder back to the older values (Ally 2.50) -- and there was NO error, NO audit
failure and NO warning. Ally ballooned to 247/533 (46.3%). It was caught by eye, days later.

THE HAZARD SHAPE, which is general: a ratified constant lives in CODE; the pipeline PUBLISHES it into
an artifact; a run from a stale branch/worktree rewrites the artifact to the older value, silently.
Nothing in the system compared "what I am about to write" against "what is already published".

WHAT THIS DOES. Before the chain writes anything, compare each guarded calibration as it exists in
CODE against the value recorded in the PUBLISHED artifact. Any difference halts the run and prints
both values. A difference is not assumed wrong -- Paul changes calibration deliberately and often --
it is assumed UNREVIEWED.

ACKNOWLEDGING AN INTENDED CHANGE. Re-run with CALIB_ACK=1 (or CALIB_ACK=<key>[,<key>...] to ack only
specific keys). The guard prints the exact command. Acknowledgement is per-run and never sticky, so a
deliberate change is a one-line decision and a silent revert is impossible to make by accident.

WHY IT IS A STANDALONE STEP AND NOT AN audit_full CHECK. audit_full's ck() failures set F, audit_full
exits 1 on any F, and run_pipeline aborts the chain on any nonzero exit -- so a calibration warning
routed through ck() would become a total chain outage. This runs FIRST instead, where a halt is clean
because no artifact has been written yet.

Read-only: opens the published artifacts and the code, writes nothing.
Usage: python calibration_guard.py            (check; exit 1 on unacknowledged drift)
       CALIB_ACK=1 python calibration_guard.py (accept all drift this run)
"""
import io, json, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

B = MB_ROOT


def _published_overall():
    """The ladder recorded in the last published category_stance.json."""
    path = os.path.join(B, 'category_stance.json')
    if not os.path.exists(path):
        return None
    with io.open(path, encoding='utf-8') as handle:
        doc = json.load(handle)
    overall = ((doc.get('thresholds') or {}).get('overall') or {})
    bands = overall.get('bands') or []
    if not bands:
        return None
    return {'scale': overall.get('scale'),
            'bands': tuple((float(b['minimum']), b['tier']) for b in bands)}


def _published_percat():
    """The per-domain stance cutoffs recorded in the last published category_stance.json."""
    path = os.path.join(B, 'category_stance.json')
    if not os.path.exists(path):
        return None
    with io.open(path, encoding='utf-8') as handle:
        th = (json.load(handle).get('thresholds') or {})
    keys = ('champion_or_defender', 'ally', 'highly_persuadable', 'persuadable', 'lean_persuadable')
    if not all(k in th for k in keys):
        return None
    return tuple((k, float(th[k])) for k in keys)


def _published_weights():
    """The pillar weights recorded in the last published ranks_python.json."""
    path = os.path.join(B, 'ranks_python.json')
    if not os.path.exists(path):
        return None
    with io.open(path, encoding='utf-8') as handle:
        ab = (json.load(handle).get('ab_weights') or {})
    return tuple(sorted((str(k), round(float(v), 9)) for k, v in ab.items())) or None


def _code_weights():
    """The ratified score policy, including zero-weight retained measures."""
    from engine.pillar_policy import serialized_weights
    return serialized_weights()


def _code_overall():
    import moneyball_core as MC
    return {'scale': MC.overall_thresholds()['scale'],
            'bands': tuple((float(c), t) for c, t in MC.OVERALL_BANDS)}


def _code_percat():
    import stance.stance_rules as SR
    return (('champion_or_defender', 0.70), ('ally', 0.35), ('highly_persuadable', 0.25),
            ('persuadable', 0.15), ('lean_persuadable', 0.05)) if not hasattr(SR, 'FIXED_BANDS') else \
        tuple((n, float(c)) for c, n in
              ((SR.FIXED_BANDS[0][0], 'champion_or_defender'), (SR.FIXED_BANDS[1][0], 'ally'),
               (SR.FIXED_BANDS[2][0], 'highly_persuadable'), (SR.FIXED_BANDS[3][0], 'persuadable'),
               (SR.FIXED_BANDS[4][0], 'lean_persuadable')))


# Each guarded item: key -> (human label, code getter, published getter).
# The published getter returns None when the artifact does not exist yet (a first-ever run), which is
# NOT drift -- there is nothing to revert.
GUARDED = {
    'overall_ladder': ('overall stance ladder (moneyball_core.OVERALL_BANDS + scale)',
                       _code_overall, _published_overall),
    'per_category_bands': ('per-domain stance cutoffs (stance_rules.FIXED_BANDS)',
                           _code_percat, _published_percat),
    'pillar_weights': ('overall pillar weights (press sentiment active but diminished)',
                       _code_weights, _published_weights),
}


def check(ack=None):
    """Return (drift, checked). drift maps key -> (label, code_value, published_value)."""
    ack = set(ack or ())
    drift, checked = {}, []
    for key, (label, code_fn, pub_fn) in sorted(GUARDED.items()):
        try:
            code_val = code_fn()
        except Exception as exc:
            # Unreadable is not unchanged; CALIB_ACK only acknowledges known drift.
            raise RuntimeError('calibration guard cannot read code value for %s; '
                               'refusing an unverified run' % key) from exc
        pub_val = pub_fn()
        checked.append(key)
        if pub_val is None:
            print('  guard: %-20s no published value yet (first run) — nothing to revert' % key)
            continue
        if code_val != pub_val and key not in ack and 'ALL' not in ack:
            drift[key] = (label, code_val, pub_val)
        elif code_val != pub_val:
            print('  guard: %-20s DRIFT ACKNOWLEDGED by CALIB_ACK' % key)
        else:
            print('  guard: %-20s matches published' % key)
    return drift, checked


def main():
    raw = (os.environ.get('CALIB_ACK') or '').strip()
    ack = ('ALL',) if raw in ('1', 'all', 'ALL', 'true') else tuple(
        s.strip() for s in raw.split(',') if s.strip())
    print('calibration guard: comparing CODE against the PUBLISHED artifacts')
    drift, checked = check(ack)
    if not drift:
        print('calibration guard: OK (%d checked%s)'
              % (len(checked), ', drift acknowledged' if ack else ''))
        return 0
    print('')
    print('!! CALIBRATION DRIFT — this run would CHANGE a published calibration. Halting before any')
    print('!! artifact is written. This is how a ratified setting gets silently reverted (2026-07-19:')
    print('!! the overall ladder was reverted Ally 3.00 -> 2.50 by a run from a stale branch).')
    for key, (label, code_val, pub_val) in sorted(drift.items()):
        print('')
        print('  [%s] %s' % (key, label))
        print('     published : %s' % (pub_val,))
        print('     code      : %s' % (code_val,))
    print('')
    print('  If this change is INTENDED, re-run with:  CALIB_ACK=%s' % ','.join(sorted(drift)))
    print('  If it is NOT intended, you are probably on a branch that is missing a ratified')
    print('  calibration — check `git log --all -S "OVERALL_BANDS" -- fec/moneyball_core.py`.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
