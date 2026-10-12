# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""B5 — blue genre-residualized refit, ONE-look evaluation (big5_improvements_prereg.md @a61820e).
Method (prereg verbatim): regress every lexical feature on genre indicators + log(text volume);
replace features with residuals; re-run the v2 behavior-calibrated fitting (same targets, same
nested 5-fold, same seed, same alpha grid) on the residualized features.
Genre indicators at the member level = the member's corpus genre COMPOSITION (share of tokens from
CREC / INTERVIEW / social) + log(total tokens). Family-MTMM bar: per-genre trait scores = the
genre-restricted featz (BIG5_SOURCES artifacts) scored with the REFIT weights; pass requires mean
same-trait cross-genre r > mean cross-trait within-genre |r| (the test the parent family failed).
Bars (locked): held-out r  A >= +0.12, C >= +0.11, O >= +0.18, E >= +0.29;  AND family MTMM pass.
On miss: current v2 stands; the failure is documented in big5_methods.md."""
import json, math, sqlite3, collections, sys
import numpy as np
from sklearn.linear_model import Ridge, LinearRegression
from sklearn.model_selection import KFold, GridSearchCV
B = MB_ROOT
SCR = os.environ.get('B5_EVAL_DIR',os.path.join(B,'outputs','research','b5eval'))

BT = json.load(open(B + r'\data\members\big5_traits.json'))
FZ = BT['featz']; FEATS = sorted(next(iter(FZ.values())))
BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
TARGET = {'A': 'cooperation_z', 'C': 'diligence_z', 'E': 'visibility_z', 'O': 'breadth_z'}
BARS = {'A': 0.12, 'C': 0.11, 'E': 0.29, 'O': 0.18}

# ---- genre composition covariates per member (tokens per source, same corpus law as big5_traits) ----
con = sqlite3.connect(B + r'\social_media.db', timeout=30); con.execute('PRAGMA query_only=1')
vol = collections.defaultdict(lambda: {'CREC': 0, 'INTERVIEW': 0, 'social': 0})
for m, s, w in con.execute("select member, source, sum(nwords) from speeches "
                           "where source in ('CREC','INTERVIEW') group by member, source"):
    vol[m][s] = w or 0
import moneyball_core as MC
_b2n = MC.bio2name()
for bg, w in con.execute("select bioguide, sum(length(text)/5) from posts where repost_of is null group by bioguide"):
    m = _b2n.get(bg)
    if m: vol[m]['social'] = int(w or 0)
con.close()
def covrow(m):
    v = vol.get(m, {'CREC': 0, 'INTERVIEW': 0, 'social': 0})
    tot = max(1, v['CREC'] + v['INTERVIEW'] + v['social'])
    return [v['CREC'] / tot, v['INTERVIEW'] / tot, v['social'] / tot, math.log1p(tot)]

ms_all = sorted(FZ)
X = np.array([[FZ[m][f] for f in FEATS] for m in ms_all])
Cv = np.array([covrow(m) for m in ms_all])
# residualize every feature on the genre covariates (OLS with intercept)
lr = LinearRegression().fit(Cv, X)
Xr = X - lr.predict(Cv)
print('B5: residualized %d members x %d features on [share_CREC, share_INT, share_social, log tokens]'
      % Xr.shape)
print('    mean |feature - genre-fit| variance retained: %.2f' % float((Xr.var(axis=0) / np.maximum(1e-9, X.var(axis=0))).mean()))

def heldout_r(Xm, y):   # lockstep with big5_lexcal_v2.heldout_r (seed 7, alpha grid, 5-fold outer / 3 inner)
    rs = []
    for tr, te in KFold(5, shuffle=True, random_state=7).split(Xm):
        g = GridSearchCV(Ridge(), {'alpha': [1, 3, 10, 30, 100, 300]}, cv=3, scoring='neg_mean_squared_error')
        g.fit(Xm[tr], y[tr]); p = g.predict(Xm[te])
        rs.append(0.0 if p.std() < 1e-9 else float(np.corrcoef(p, y[te])[0, 1]))
    return float(np.mean(rs))

midx = {m: i for i, m in enumerate(ms_all)}
ho = {}; models = {}
for tr, tgt in TARGET.items():
    ms = [m for m in ms_all if m in BD and tgt in BD[m] and BD[m][tgt] is not None]
    Xm = Xr[[midx[m] for m in ms]]; y = np.array([BD[m][tgt] for m in ms])
    ho[tr] = heldout_r(Xm, y)
    g = GridSearchCV(Ridge(), {'alpha': [1, 3, 10, 30, 100, 300]}, cv=5, scoring='neg_mean_squared_error')
    g.fit(Xm, y); models[tr] = g.best_estimator_
    print('  %-2s <- %-14s n=%-3d  RESIDUALIZED held-out r=%+.3f  (bar >= %+.2f)' % (tr, tgt, len(ms), ho[tr], BARS[tr]))

# ---- family MTMM under the refit: per-genre featz scored with the refit weights ----
GEN = {}
for gsrc in ('CREC', 'INTERVIEW', 'social'):
    gz = json.load(open(SCR + '\\big5_traits_%s.json' % gsrc))['featz']
    GEN[gsrc] = gz
def gscore(tr, gsrc, m):
    gz = GEN[gsrc].get(m)
    if gz is None: return None
    return float(models[tr].predict(np.array([[gz[f] for f in FEATS]]))[0])
trs = list(TARGET)
pairs = [('CREC', 'INTERVIEW'), ('CREC', 'social'), ('INTERVIEW', 'social')]
same, cross = [], []
for g1, g2 in pairs:
    common = [m for m in ms_all if m in GEN[g1] and m in GEN[g2]]
    for t1 in trs:
        a = np.array([gscore(t1, g1, m) for m in common]); b = np.array([gscore(t1, g2, m) for m in common])
        if a.std() > 0 and b.std() > 0: same.append(float(np.corrcoef(a, b)[0, 1]))
for gsrc in GEN:
    common = [m for m in ms_all if m in GEN[gsrc]]
    for i, t1 in enumerate(trs):
        for t2 in trs[i + 1:]:
            a = np.array([gscore(t1, gsrc, m) for m in common]); b = np.array([gscore(t2, gsrc, m) for m in common])
            if a.std() > 0 and b.std() > 0: cross.append(abs(float(np.corrcoef(a, b)[0, 1])))
mt_same, mt_cross = float(np.mean(same)), float(np.mean(cross))
print('  family MTMM: same-trait cross-genre %.3f  vs  cross-trait within-genre %.3f  -> %s'
      % (mt_same, mt_cross, 'PASS' if mt_same > mt_cross else 'FAIL'))

barhits = [(t, ho[t] >= BARS[t]) for t in TARGET]
ok = all(h for _, h in barhits) and mt_same > mt_cross
for t, h in barhits: print('  BAR %s held-out >= %+.2f: %s (%+.3f)' % (t, BARS[t], 'CLEAR' if h else 'MISS', ho[t]))
print('  BAR family MTMM (same > cross): %s' % ('CLEAR' if mt_same > mt_cross else 'MISS'))
print('B5 VERDICT: %s' % ('PASS — promote genre-residualized refit'
                          if ok else 'MISS — current v2 stands; failure documented (lexical signal may be inseparable from genre)'))

# ---- ROUND 2 (--r2): composed family per big5_round2_prereg.md @54d0f7c — ONE look ----
if '--r2' in sys.argv:
    print('\n===== ROUND 2: composed family (resid A/C/O + v2 E) =====')
    BT2 = json.load(open(B + r'\data\members\big5_v2.json'))['traits']
    comp = {}   # member -> {A,C,O from residualized refit; E from v2}
    for tr in ('A', 'C', 'O'):
        pred = models[tr].predict(Xr)
        mu, sd = float(pred.mean()), float(pred.std()) or 1.0
        for i, m in enumerate(ms_all): comp.setdefault(m, {})[tr] = (pred[i] - mu) / sd
    for m in ms_all:
        if m in BT2: comp[m]['E'] = BT2[m].get('E', 0.0)
    common = [m for m in ms_all if m in comp and 'E' in comp[m]]
    trs4 = ['A', 'C', 'E', 'O']
    Zc = np.array([[comp[m][t] for t in trs4] for m in common])
    # R2-1 discriminant
    offs = [abs(float(np.corrcoef(Zc[:, i], Zc[:, j])[0, 1])) for i in range(4) for j in range(i + 1, 4)]
    disc = sum(offs) / len(offs)
    # R2-2 known-groups (O-, C+, E-, A-)
    import moneyball_core as MC
    P = MC.party()
    EXP = {'O': -1, 'C': +1, 'E': -1, 'A': -1}
    kg = 0
    for ti, t in enumerate(trs4):
        rs = [Zc[i][ti] for i, m in enumerate(common) if P.get(m) == 'R']
        ds = [Zc[i][ti] for i, m in enumerate(common) if P.get(m) == 'D']
        if rs and ds and ((sum(rs)/len(rs) - sum(ds)/len(ds)) > 0) == (EXP[t] > 0): kg += 1
    # R2-3 A/C/O-subfamily MTMM with refit weights on per-genre featz
    same, cross = [], []
    aco = ['A', 'C', 'O']
    for g1, g2 in pairs:
        cm = [m for m in ms_all if m in GEN[g1] and m in GEN[g2]]
        for t in aco:
            a = np.array([gscore(t, g1, m) for m in cm]); b = np.array([gscore(t, g2, m) for m in cm])
            if a.std() > 0 and b.std() > 0: same.append(float(np.corrcoef(a, b)[0, 1]))
    for gsrc in GEN:
        cm = [m for m in ms_all if m in GEN[gsrc]]
        for i, t1 in enumerate(aco):
            for t2 in aco[i + 1:]:
                a = np.array([gscore(t1, gsrc, m) for m in cm]); b = np.array([gscore(t2, gsrc, m) for m in cm])
                if a.std() > 0 and b.std() > 0: cross.append(abs(float(np.corrcoef(a, b)[0, 1])))
    mts, mtc = float(np.mean(same)), float(np.mean(cross))
    bars = [('R2-1 discriminant <= 0.30', disc <= 0.30, '%.3f (worst pair check: %s)' % (disc, max(offs))),
            ('R2-2 known-groups >= 3/4', kg >= 3, '%d/4' % kg),
            ('R2-3 ACO MTMM same > cross', mts > mtc, 'same %.3f vs cross %.3f' % (mts, mtc))]
    ok = all(b[1] for b in bars)
    for nm, hit, det in bars: print('  BAR %-32s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', det))
    print('R2 VERDICT: %s' % ('PASS — promote composed family' if ok else 'MISS — v2 stands, documented'))
