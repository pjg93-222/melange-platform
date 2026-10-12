# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""ROUND-4 N SUCCESSOR-GATE DIAGNOSTIC (2026-07-12).

Synthetic only: test whether the unchanged Empath instrument can recover an orthogonal planted
true r=.40 N signal on the CURRENT reactivity-covered roster. This implements the five pinned
seeds and bars 1-4 required by big5_n2_prereg.md. It never pairs a real text prediction with a
real criterion value and writes no result artifact. BI.featurize may refresh its text-only cache
if the corpus fingerprint changed. A >=3/5 result licenses a prereg lock; it is not trait evidence.
"""
import argparse
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
_s.stdout.reconfigure(encoding='utf-8', errors='replace')
import members.b5_behavioral_instrument as BI
import members.b5_n_matrix as NM

PIN5 = [424242, 515151, 616161, 717171, 818181]
CORE = NM.CORE
JOBS = [1]

def oof(X, y):
    """BI.oof verbatim with n_jobs exposed; fits and fold seeds are unchanged."""
    pr = np.zeros((len(BI.SEEDS), len(y)))
    for i, seed in enumerate(BI.SEEDS):
        for tr, te in KFold(5, shuffle=True, random_state=seed).split(X):
            fit = GridSearchCV(Ridge(), {'alpha': BI.ALPHAS}, cv=3,
                               scoring='neg_mean_squared_error', n_jobs=JOBS[0])
            fit.fit(X[tr], y[tr])
            pr[i, te] = fit.predict(X[te])
    return pr.mean(0)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=1,
                    help='GridSearchCV worker count; changes latency only, not numerics')
    args = ap.parse_args()
    JOBS[0] = args.jobs

    BD, _VV, _P, MEM, alltext = BI.load_corpus()
    mask = np.array([(BD[m] or {}).get('reactivity_z') is not None for m in MEM])
    # Criterion values are discarded before any model fit; only the preregistered coverage mask remains.
    del BD
    print('N successor diagnostic: SYNTHETIC ONLY — no real trait×criterion pairing')
    print('rosters: MEM=%d | MEM_N=%d | gate=250 | jobs=%d' %
          (len(MEM), int(mask.sum()), JOBS[0]), flush=True)
    if int(mask.sum()) < 250:
        print('GATE REFUSED: MEM_N below 250; no prereg lock permitted')
        return

    X = BI.featurize(MEM, alltext)
    Xs = X[mask]
    runs = []
    for seed in PIN5:
        rng = np.random.default_rng(seed)
        yc, yn = NM.plant_set(X, Xs, mask, rng)
        pc = {trait: oof(X, yc[trait]) for trait in CORE}
        pn = oof(Xs, yn)
        own, perm_p, _ = BI.exact_perm_p(pn, yn)
        row = all(abs(own) >= abs(NM.rr(pn, yc[c][mask])) for c in ['O', 'C', 'E', 'A']) and own > 0
        col = all(abs(own) > abs(NM.rr(pc[c][mask], yn)) for c in CORE)
        passed = own >= .15 and perm_p < .05 and row and col
        runs.append((seed, own, perm_p, row, col, passed))
        print('  seed %d: own %+.3f p %.4f row %s col %s -> bars1-4 %s' %
              (seed, own, perm_p, row, col, 'PASS' if passed else 'miss'), flush=True)

    n_pass = sum(x[-1] for x in runs)
    median = float(np.median([x[1] for x in runs]))
    print('N SUCCESSOR SUMMARY: median own %+.3f | bars1-4 %d/5 -> %s' %
          (median, n_pass, 'GATE CLEARED; prereg may lock' if n_pass >= 3
           else 'GATE REFUSED; no look permitted'))

if __name__ == '__main__':
    main()
