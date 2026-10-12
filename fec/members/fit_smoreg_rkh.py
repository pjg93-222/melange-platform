# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""SMOreg calibrated to CONGRESS (user-directed 2026-07-03: the RKH papers measured the Big Five of
actual members — use them). Targets = Ramey/Klingler/Hollibaugh's published per-member trait
estimates (replication data doi:10.7910/DVN/JDH6KR, congressbios.tab *_avg columns, real ICPSR ids),
joined to the current roster via the voteview rosters (146 overlap members). Features = OUR
standardized per-member category rates (big5_traits.json 'featz' — the exact vectors the scorer
uses). Per trait: 5-fold CV over ridge alphas + linear SVR, HELD-OUT Pearson r vs RKH as the
criterion, compared against the ACTIVE weights' r on the same folds. Winner per trait -> candidate
weights (same {trait:{feature:weight}} format the big5 hook consumes).
Caveats printed with the results: RKH personas are 1996-2014 floor speech; ours are 2025-26
speech+social — the fit is a persona-transfer calibration, not a claim of measuring 2014 traits."""
import csv, io, json, os, sys, math, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.svm import LinearSVR
from sklearn.model_selection import KFold

B = MB_ROOT
RKH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'liwc', 'rkh')

rkh = {}
with open(os.path.join(RKH, 'congressbios.tab'), encoding='utf-8', errors='replace') as fh:
    for r in csv.DictReader(fh, delimiter='\t'):
        try:
            i = int(float(r['icpsr']))
            v = {k: float(r[k + '_avg']) for k in ('openn', 'consc', 'extra', 'agree', 'emoti')}
        except (ValueError, KeyError, TypeError):
            continue
        rkh[i] = v
b2i = {}
for cn in (119, 118, 117):
    for r in csv.DictReader(open(os.path.join(B, 'votes', 'HS%d_members.csv' % cn), encoding='utf-8')):
        bg = (r.get('bioguide_id') or '').strip()
        try: ic = int(float(r.get('icpsr') or ''))
        except ValueError: continue
        if bg and bg not in b2i: b2i[bg] = ic

BT = json.load(open(B + r'\data\members\big5_traits.json'))
FZ = BT.get('featz')
if not FZ:
    print('!! big5_traits.json has no featz block — rerun big5_traits.py first'); sys.exit(1)
C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
FEATS = sorted(next(iter(FZ.values())))
# B&L persona cue features now live INSIDE featz (big5_traits owns all features since 2026-07-03 —
# uncovered members are mean-imputed there), so no separate merge here: featz keys drive everything.
rowsX = []; rowsY = []; names = []
for m, info in C.items():
    ic = b2i.get(info.get('bioguide') or '')
    if ic in rkh and m in FZ:
        rowsX.append([FZ[m][f] for f in FEATS]); rowsY.append(rkh[ic]); names.append(m)
X = np.array(rowsX)
print('overlap training set: %d members x %d features' % (X.shape[0], X.shape[1]))

ACTIVE = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'liwc', 'mairesse_smoreg.json'), encoding='utf-8'))
MAPS = (('O', 'openn'), ('C', 'consc'), ('E', 'extra'), ('A', 'agree'), ('ES', 'emoti'))

def pearson(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if a.std() == 0 or b.std() == 0: return 0.0
    return float(np.corrcoef(a, b)[0, 1])

GRID = ([('ridge a=%g' % a, Ridge(alpha=a)) for a in (3, 10, 30, 100, 300)] +
        [('svr C=%g' % c, LinearSVR(C=c, epsilon=0.1, max_iter=20000, random_state=7)) for c in (0.01, 0.03, 0.1)])
OUT = {}
print('%-3s %-12s %-28s %s' % ('', 'baseline r', 'best model (CV held-out r)', 'verdict'))
for t, tgt in MAPS:
    y = np.array([d[tgt] for d in rowsY])
    kf = list(KFold(5, shuffle=True, random_state=7).split(X))
    # baseline: the ACTIVE weights applied to features (linear score), held-out r on same folds
    w0 = np.array([ACTIVE[t].get(f, 0.0) for f in FEATS])
    base = np.mean([pearson(X[te] @ w0, y[te]) for _, te in kf])
    best = (None, -9, None)
    for nm, mdl in GRID:
        rs = []
        for tr, te in kf:
            mdl.fit(X[tr], y[tr]); rs.append(pearson(mdl.predict(X[te]), y[te]))
        cv = float(np.mean(rs))
        if cv > best[1]: best = (nm, cv, mdl)
    nm, cv, mdl = best
    mdl.fit(X, y)
    coef = mdl.coef_ if hasattr(mdl, 'coef_') else mdl.coef_
    verdict = 'REFIT WINS' if cv > base + 0.02 else ('tie' if cv > base - 0.02 else 'keep active')
    print('%-3s %+11.3f  %-16s %+6.3f      %s' % (t, base, nm, cv, verdict))
    OUT[t] = {'weights': {FEATS[i]: round(float(np.ravel(coef)[i]), 4) for i in range(len(FEATS))},
              'cv_r': round(cv, 3), 'baseline_r': round(base, 3), 'model': nm, 'verdict': verdict}

cand = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'liwc', 'rkh_calibrated.json.candidate')
json.dump(OUT, open(cand, 'w'), indent=1)
print('\nwrote %s' % cand)
print('promotion rule: per-trait — take the refit ONLY where verdict=REFIT WINS; party-sign check afterwards is a veto.')
