# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Behavior-anchored per-trait text instrument (big5_behavioral_instrument_prereg.md @d630fe3).
For each trait, Empath features supervised (held-out ridge CV) to that trait's BEHAVIORAL criterion,
then the discriminant test: does each trait's held-out read predict its OWN behavior best (diagonal-
dominant)? The C recipe generalized. ES criterion ladder (pre-declared order): (1) residual-GMP
(GMP residualized on cooperation + extremity + party); (2) affect lability (std of per-tweet Empath
sentiment, sign-flipped). A criterion is VALID only if the ES model is its strongest text predictor.

2026-07-09 hardening (psych-audit session, PRE-look — bars untouched):
  * exact permutation p RESTORED per the locked prereg ("exact perm-p < .05"): 5000 label
    permutations against the FIXED out-of-fold predictions (the b5_es_crossmodal.py method — costs
    seconds, so the parametric shortcut is retired; parametric printed as cross-check only).
  * __main__ gate + modes: this file previously took THE LOOK at import time (import==execution
    hazard). Now: --simulate = machinery validation on PLANTED + NULL targets (never computes any
    real trait x criterion pairing = no look consumed); --look = THE ONE LOOK, hard-gated on the
    prereg containing no RESULT section yet (mirrors b5_panel_score.py).
  * Empath features cached (b5_empath_cache.json, per-member text-hash keyed) so simulate + look
    share one featurization.
ONE LOOK."""
import json, sqlite3, hashlib, argparse
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
from empath import Empath
sys = _s; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import moneyball_core as MC
B = MB_ROOT
PREREG = B + r'\big5_behavioral_instrument_prereg.md'
CACHEF = B + r'\data\members\b5_empath_cache.json'
TR = ['O', 'C', 'E', 'A', 'ES']
BEH = {'A': 'cooperation_z', 'C': 'diligence_z', 'E': 'visibility_z', 'O': 'breadth_z'}
ALPHAS = [1, 3, 10, 30, 100, 300, 1000]; SEEDS = [7, 17, 29, 43, 61]

def load_corpus():
    BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
    VV = json.load(open(B + r'\data\members\voteview_consistency.json', encoding='utf-8'))['members']
    P = MC.party()
    con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
    SPE = {m: (t or '') for m, t in con.execute("select member, group_concat(text,' ') from speeches group by member")}
    b2n = MC.bio2name(); TW = {}
    for bg, t in con.execute("select bioguide, group_concat(text,' ') from posts where repost_of is null group by bioguide"):
        m = b2n.get(bg)
        if m: TW[m] = (t or '')
    con.close()
    def alltext(m): return (SPE.get(m, '') + ' ' + TW.get(m, ''))[:600000]
    MEM = [m for m in BD if m in VV and P.get(m) in ('D', 'R')
           and all((BD[m] or {}).get(v) is not None for v in BEH.values())
           and len(alltext(m)) > 2000]
    return BD, VV, P, MEM, alltext

def featurize(MEM, alltext):
    """Empath category rates per member, text-hash cached (featurization never sees criteria)."""
    lex = Empath(); CATS = sorted((lex.analyze('a b c', normalize=True) or {}).keys())
    cache = json.load(open(CACHEF, encoding='utf-8')) if _o.path.exists(CACHEF) else {}
    dirty = 0
    rows = []
    for m in MEM:
        tx = alltext(m); h = hashlib.md5(tx.encode('utf-8', 'replace')).hexdigest()[:16]
        c = cache.get(m)
        if not c or c.get('h') != h or len(c.get('v', [])) != len(CATS):
            v = lex.analyze(tx, normalize=True) or {}
            c = {'h': h, 'v': [v.get(cat, 0.0) for cat in CATS]}
            cache[m] = c; dirty += 1
            if dirty % 25 == 0: MC.dump_atomic(cache, CACHEF, indent=0)
        rows.append(c['v'])
    if dirty: MC.dump_atomic(cache, CACHEF, indent=0)
    X = np.array(rows, float)
    xsd = X.std(0, ddof=1); xsd[xsd < 1e-12] = 1
    X = (X - X.mean(0)) / xsd
    print('features: %d members x %d Empath cats (%d re-featurized, rest cached)' % (X.shape[0], X.shape[1], dirty))
    return X

def criteria(BD, VV, P, MEM):
    coop = np.array([BD[m]['cooperation_z'] for m in MEM]); dilig = np.array([BD[m]['diligence_z'] for m in MEM])
    vis = np.array([BD[m]['visibility_z'] for m in MEM]); breadth = np.array([BD[m]['breadth_z'] for m in MEM])
    gmp = np.array([VV[m]['gmp'] for m in MEM]); ext = np.array([VV[m]['extremity'] for m in MEM])
    party = np.array([1.0 if P.get(m) == 'R' else 0.0 for m in MEM])
    def resid(v, Z):
        Z1 = np.column_stack([np.ones(len(v)), Z]); b, *_ = np.linalg.lstsq(Z1, v, rcond=None); return v - Z1 @ b
    residgmp = resid(gmp, np.column_stack([coop, ext, party]))   # ES attempt (1): consistency NOT explained by A/extremity/party
    return {'O': breadth, 'C': dilig, 'E': vis, 'A': coop, 'ES': residgmp}

def affect_lability(MEM):
    """ES attempt (2): std of per-tweet Empath sentiment (posemo-negemo) over time, sign-flipped
    (volatile affect = low ES). >=30 original tweets required; others None. Cached."""
    CF = B + r'\data\members\b5_lability_cache.json'
    cache = json.load(open(CF, encoding='utf-8')) if _o.path.exists(CF) else {}
    lex = Empath()
    b2n = MC.bio2name()
    con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
    per = {}
    for bg, in con.execute("select distinct bioguide from posts where repost_of is null"):
        m = b2n.get(bg)
        if not m or m not in set(MEM): continue
        if m in cache: per[m] = cache[m]; continue
        txts = [t for (t,) in con.execute("select text from posts where bioguide=? and repost_of is null", (bg,)) if t and len(t) > 20]
        if len(txts) < 30: cache[m] = None; per[m] = None; continue
        ss = []
        for t in txts[:2000]:
            a = lex.analyze(t, normalize=True) or {}
            ss.append((a.get('positive_emotion', 0.0)) - (a.get('negative_emotion', 0.0)))
        s = float(np.std(ss, ddof=1)) if len(ss) > 1 else None
        cache[m] = s; per[m] = s
        if len(cache) % 25 == 0: MC.dump_atomic(cache, CF)
    con.close(); MC.dump_atomic(cache, CF)
    vals = np.array([per.get(m) if per.get(m) is not None else np.nan for m in MEM], float)
    ok = ~np.isnan(vals)
    if ok.sum() < 100: return None, ok
    z = np.full(len(MEM), 0.0); z[ok] = -(vals[ok] - vals[ok].mean()) / (vals[ok].std(ddof=1) or 1.0)   # sign-flipped: volatility -> LOW ES
    return z, ok

def oof(X, y):
    pr = np.zeros((len(SEEDS), len(y)))
    for i, s in enumerate(SEEDS):
        for tr, te in KFold(5, shuffle=True, random_state=s).split(X):
            g = GridSearchCV(Ridge(), {'alpha': ALPHAS}, cv=3, scoring='neg_mean_squared_error'); g.fit(X[tr], y[tr]); pr[i, te] = g.predict(X[te])
    return pr.mean(0)

def exact_perm_p(pred, y, K=5000, seed=20260708):
    """Exact permutation p per the locked prereg: label permutations against the FIXED out-of-fold
    prediction vector (the b5_es_crossmodal.py method). One-sided, r>0."""
    obs = float(np.corrcoef(pred, y)[0, 1])
    rng = np.random.default_rng(seed)
    null = np.array([float(np.corrcoef(pred, rng.permutation(y))[0, 1]) for _ in range(K)])
    p = float((np.sum(null >= obs) + 1) / (K + 1))
    from scipy import stats
    n = len(y); t = obs * np.sqrt((n - 2) / max(1e-9, 1 - obs * obs)); para = float(stats.t.sf(t, n - 2))
    return obs, p, para

def run_matrix(X, CRIT, label):
    PRED = {t: oof(X, CRIT[t]) for t in TR}
    print('\n=== [%s] discriminant matrix: corr(held-out pred_row, criterion_col) ===' % label)
    print('  model\\crit ' + ''.join('%9s' % c for c in TR))
    M = {}
    for pt in TR:
        row = []
        for ct in TR:
            r = float(np.corrcoef(PRED[pt], CRIT[ct])[0, 1]); M[(pt, ct)] = r; row.append(r)
        print('  pred_%-3s  ' % pt + ''.join('%+9.3f' % x for x in row))
    print('\n=== [%s] per-trait pass test (held-out own r, exact perm-p, diagonal-dominant) ===' % label)
    npass = 0; det = {}
    for t in TR:
        own, pp, para = exact_perm_p(PRED[t], CRIT[t])
        dom = all(abs(own) >= abs(M[(t, c)]) for c in TR if c != t) and own > 0
        ok = own >= 0.15 and pp < 0.05 and dom
        npass += ok; det[t] = (own, pp, dom, ok)
        print('  %-3s own r=%+.3f exact perm-p=%.4f (parametric %.4f) | diagonal-dominant=%s | %s' % (t, own, pp, para, dom, 'PASS' if ok else 'MISS'))
    return PRED, M, npass, det

def es_criterion_valid(PRED, crit):
    """Prereg validity rule: the ES criterion is valid only if the ES model is its strongest text
    predictor (else it is a proxy for another trait's criterion)."""
    rs = {t: abs(float(np.corrcoef(PRED[t], crit)[0, 1])) for t in TR}
    best = max(rs, key=rs.get)
    return best == 'ES', rs

def simulate(X, CRIT_real):
    """Machinery validation, NO look consumed: (a) PLANTED distinct signals -> must recover ~5/5;
    (b) NULL (real criteria permuted, pairing destroyed) -> must recover 0/5 with calibrated p.
    Real trait x criterion correlations are never computed here."""
    n, k = X.shape
    rng = np.random.default_rng(11)
    print('\n----- SIMULATE (a): planted distinct per-trait signals (expect 5/5 PASS) -----')
    cats = rng.permutation(k)
    CRITp = {}
    for i, t in enumerate(TR):
        w = np.zeros(k); idxs = cats[i * 20:(i + 1) * 20]; w[idxs] = rng.normal(0, 1, len(idxs))
        sig = X @ w; sig = (sig - sig.mean()) / (sig.std(ddof=1) or 1)
        CRITp[t] = sig + rng.normal(0, 2.0, n)   # ~r 0.4 planted
    _, _, np1, _ = run_matrix(X, CRITp, 'SIM planted')
    print('\n----- SIMULATE (b): null = real criteria PERMUTED (expect 0/5, flat p) -----')
    CRITn = {t: rng.permutation(CRIT_real[t]) for t in TR}
    _, _, np0, _ = run_matrix(X, CRITn, 'SIM null')
    okA = np1 >= 4; okB = np0 == 0
    print('\nSIM VERDICT: planted %d/5 (need >=4) %s | null %d/5 (need 0) %s -> machinery %s' %
          (np1, 'OK' if okA else 'FAIL', np0, 'OK' if okB else 'FAIL', 'VALIDATED' if okA and okB else 'NOT validated'))
    return okA and okB

def the_look(X, CRIT, MEM):
    pre = open(PREREG, encoding='utf-8').read()
    if '\n## RESULT' in pre:   # NEWLINE-anchored heading match (@72690c9 lesson: prose quotes of the heading must not trip the gate)
        print('!! LOOK REFUSED: prereg already contains a RESULT section (one look only).'); return
    print('THE LOOK (one look, bars locked @d630fe3): N=%d' % len(MEM))
    PRED, M, npass, det = run_matrix(X, CRIT, 'LOOK: ES=residual-GMP')
    valid1, rs1 = es_criterion_valid(PRED, CRIT['ES'])
    print('\nES attempt (1) residual-GMP criterion validity (ES model must be its strongest predictor):')
    print('  |r| by model: ' + ', '.join('%s %.3f' % (t, rs1[t]) for t in TR) + ' -> %s' % ('VALID' if valid1 else 'INVALID (proxy)'))
    if not valid1 or not det['ES'][3]:
        print('\nES attempt (2): affect lability (pre-declared fallback) ...')
        lab, okmask = affect_lability(MEM)
        if lab is None:
            print('  lability coverage <100 members -> attempt (2) unavailable; ES honest count stands on attempt (1).')
        else:
            CRIT2 = dict(CRIT); CRIT2['ES'] = lab
            PRED2, M2, npass2, det2 = run_matrix(X, CRIT2, 'LOOK: ES=affect-lability')
            valid2, rs2 = es_criterion_valid(PRED2, lab)
            print('\nES attempt (2) criterion validity: ' + ', '.join('%s %.3f' % (t, rs2[t]) for t in TR) + ' -> %s' % ('VALID' if valid2 else 'INVALID (proxy)'))
            if valid2 and det2['ES'][3] and not (valid1 and det['ES'][3]):
                print('  (per prereg ladder: attempt (1) failed, attempt (2) is the ES criterion of record)')
                npass = npass - (1 if det['ES'][3] else 0) + 1
    print('\n===== VERDICT: %d/5 traits pass (held-out r>=+0.15, exact perm-p<.05, diagonal-dominant) =====' % npass)
    print('Honest-count rule: if no ES attempt yields a VALID criterion with a pass, the max possible is 4/5 (prereg).')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--simulate', action='store_true', help='machinery validation on planted+null targets (no look consumed)')
    ap.add_argument('--look', action='store_true', help='THE ONE LOOK (gated: refuses if prereg has a RESULT)')
    a = ap.parse_args()
    BD, VV, P, MEM, alltext = load_corpus()
    print('Behavior-anchored instrument: N=%d eligible members' % len(MEM))
    X = featurize(MEM, alltext)
    if a.simulate:
        CRIT = criteria(BD, VV, P, MEM)
        simulate(X, CRIT)
    elif a.look:
        CRIT = criteria(BD, VV, P, MEM)
        the_look(X, CRIT, MEM)
    else:
        pre = open(PREREG, encoding='utf-8').read() if _o.path.exists(PREREG) else ''
        print('status: prereg %s | look %s | run --simulate first, then --look' %
              ('LOCKED @d630fe3' if 'Locked' in pre or 'locked' in pre else 'MISSING',
               'ALREADY TAKEN' if '\n## RESULT' in pre else 'not yet taken'))
