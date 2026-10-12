#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CM-1 reciprocity CORROBORATION look for the competitiveness multiplier c (prereg: compet_reciprocity_prereg.md).

    python fec/engine/compet_reciprocity_look.py              status only (lock / look / fingerprints); reads no outcome
    python fec/engine/compet_reciprocity_look.py --simulate   lock-time battery: frozen c + a pinned money stand-in,
                                                              synthetic outcomes only; never opens an outcome artifact
    python fec/engine/compet_reciprocity_look.py --look       THE ONE LOOK (on/after 2028-02-15): gates -> outcome-alone
                                                              voids -> money-alone voids -> c opened -> in-process
                                                              battery on the real design -> look -> RESULT-R text

Import-safe: nothing runs on import. No LLM calls anywhere in this file. It writes nothing; the RESULT is printed for
verbatim appending to the prereg (same session). Text pins hash LF-normalised bytes (core.autocrlf=true in this repo).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PREREG = os.path.join(ROOT, 'compet_reciprocity_prereg.md')
LANE = os.path.join(ROOT, 'audits', 'compet_reciprocity')
C_FROZEN = os.path.join(LANE, 'competitiveness_c_frozen_20261011.json')
OUTCOME = os.path.join(LANE, 'collection', 'outcome_y.json')
MONEY = os.path.join(LANE, 'collection', 'money_cu.json')
LOOK_NOT_BEFORE = dt.date(2028, 2, 15)

# Pinned at lock (LF-normalised sha256). The c pin is the D-107 artifact (03be7ff38 code reproduces it byte for byte);
# the code pins are the outcome-classification rules of record (bill classifier + admission rule + role weights),
# attested line by line in the COLLECTION RECORD (classification runs at collection from the lock-commit copies).
PIN_C = '0b972b1ac33c3d0693824418674ab968b8f707dafd60e6bca26368b2f96aaefb'
PIN_CODE = {
    'fec/legislation/refresh_sentinel_admissions.py': '8ec5c85e5239267865cc1f83b6215f31b4aa0180f5a29638008fc42d1dd28bc1',
    'fec/legislation/sentinel_admissions.py': 'be1655e1f6f87abbd42083c2699acfc92d3f8cfbff0f88e19718d869a9101453',
    'fec/bill_rules.py': '650d2e9f5367f90159aff0d71c5372ed8ad4e55013759f52947ed4db18e2b422',
    'fec/engine/primacy_factors.py': '51db75075ce2c67d362a8e23f470a00fcb1864131e37e5ff5b66ea72dfffb8c0',
    'fec/legislation_measure_key.py': 'f0de929be84a5eaba900727fa3cd45465f1e14aa988d464fb4ca815733163b05',
}
# Lock-time battery money stand-in: data/engine/congestion.json as of 2026-10-11 (raw-byte sha; partial 2026 cycle).
STANDIN_MONEY = os.path.join(ROOT, 'data', 'engine', 'congestion.json')
PIN_STANDIN_RAW = '0eaf6bbcc3fd4d4bc3cfc038d561aa8b2bbd46462760767ab330e4b27c9e873c'

DELEGATE_STATES = {'PR', 'DC', 'GU', 'VI', 'AS', 'MP'}
SEEDS = {'a_planted': 424242, 'b_null': 535353, 'c_confound': 646464, 'd_true_confound': 757575}
LOCK_SUBSAMPLE_SEED = 919191
REPS = 200
RHO_PLANT = 0.15
LOCK_PI = 0.45                       # lock-time nonzero share of y (look time: the observed share)
BAT = {'a_min_t1': 0.50, 'a_min_median_r': 0.10, 'b_max_t1': 0.05, 'b_max_t4': 0.05,
       'c_max_t1': 0.05, 'c_max_t4': 0.05, 'd_min_t1': 0.50, 'max_fit_fail': 0.02}
MIN_N, MIN_NONZERO, MIN_MONEY_VERIFIED, MIN_STRATUM_N = 300, 0.10, 0.95, 30
COLS = ['const', 'c', 'L', 'cL', 'a0', 'partyR', 'senate']
WORDS = {'T1': 'the competitive-seat premium is corroborated as an association; constants remain uncalibrated',
         'T2': 'directional, not corroborated', 'T3': 'uncorroborated, never disproven',
         'T4': 'INDICTED: the D-104 reversal condition is met'}


# ---------------------------------------------------------------- hashing and gates

def sha_lf(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read().replace(b'\r\n', b'\n')).hexdigest()


def sha_raw(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def fingerprints():
    """The look's own input pin: the frozen c. (Collection artifacts are pinned by the addenda; the classification
    code is attested in the COLLECTION RECORD, because the look never executes it.)"""
    return {'c': sha_lf(C_FROZEN) if os.path.exists(C_FROZEN) else None}


def fingerprints_ok(fp):
    return fp.get('c') == PIN_C


def _addendum(text, heading, keys):
    m = re.search(r'\n## %s\b(.*?)(?=\n## |\Z)' % heading, text, re.S)
    if not m:
        return None
    pins = dict(re.findall(r'\b(%s):\s*`?([0-9a-f]{64})`?' % '|'.join(keys), m.group(1)))
    return pins if set(pins) == set(keys) else None


def collection_record(text):
    """MONEY RECORD (2026-12, pins money before any outcome exists) + COLLECTION RECORD (2028, restates the money pin,
    pins the outcome, and attests every pinned classification file by path and sha256 on one line). Both are
    newline-anchored H2 addenda; the two money pins must agree."""
    money = _addendum(text, 'MONEY RECORD', ['money_sha256'])
    coll = _addendum(text, 'COLLECTION RECORD', ['outcome_sha256', 'money_sha256'])
    if money is None or coll is None or money['money_sha256'] != coll['money_sha256']:
        return None
    body = re.search(r'\n## COLLECTION RECORD\b(.*?)(?=\n## |\Z)', text, re.S).group(1).splitlines()
    for rel, sha in PIN_CODE.items():
        if not any(rel in line and sha in line for line in body):
            return None
    return coll


def is_locked(text):
    """Line-anchored: a prose quote of the lock line must not lock the file (the RESULT-heading lesson, @72690c9)."""
    return re.search(r'(?m)^STATUS: LOCKED\b', text) is not None


def gate(text, fp, today, sha=sha_lf, exists=os.path.exists):
    """None when the look may run, else the refusal line. Pure (inputs injected) so the kernel test can drive it."""
    if not text:
        return '!! prereg ABSENT'
    if not is_locked(text):
        return '!! LOOK REFUSED: prereg not LOCKED'
    if '\n## RESULT-R' in text:                 # newline-anchored (@72690c9 lesson)
        return '!! LOOK REFUSED: RESULT-R already recorded (one look only)'
    if not fingerprints_ok(fp):
        return '!! LOOK REFUSED (not consumed): input fingerprint drift vs pinned lock values'
    if today < LOOK_NOT_BEFORE:
        return '!! LOOK REFUSED (not consumed): look date is on or after %s' % LOOK_NOT_BEFORE.isoformat()
    pins = collection_record(text)
    if pins is None:
        return '!! LOOK REFUSED (not consumed): MONEY RECORD / COLLECTION RECORD addenda absent, incomplete, disagreeing or not attesting the pinned classification code'
    if not (exists(OUTCOME) and exists(MONEY)):
        return '!! LOOK REFUSED (not consumed): collection artifact absent'
    if sha(OUTCOME) != pins['outcome_sha256'] or sha(MONEY) != pins['money_sha256']:
        return '!! LOOK REFUSED (not consumed): collection artifact sha256 differs from the COLLECTION RECORD'
    return None


# ---------------------------------------------------------------- statistics
# Primary model: Tobit (left-censored at 0) of asinh(y) on X; the hypothesis lives on the latent propensity scale.
# Draft 1 (OLS on asinh(y)) failed its own confound check pre-lock: the zero floor turns main effects of c and money
# into an additive-scale "interaction" (prereg, Pre-lock design history). Route 1 = own analytic-gradient ML +
# HC1-scaled sandwich; route 2 = statsmodels GenericLikelihoodModel (its own optimizer, numeric scores and Hessian).

def _resid(v, Z):
    beta, *_ = np.linalg.lstsq(Z, v, rcond=None)
    return v - Z @ beta


def _norm():
    from scipy.stats import norm
    return norm


def _tobit_parts(theta, a, X):
    """Per-observation log-likelihood and scores (columns: beta..., log sigma)."""
    N = _norm()
    k = X.shape[1]
    b, s = theta[:k], math.exp(theta[k])
    m = X @ b
    pos = a > 0
    ll = np.empty_like(a)
    g = np.empty((a.size, k + 1))
    r = (a[pos] - m[pos]) / s
    ll[pos] = N.logpdf(r) - math.log(s)
    g[pos, :k] = (r / s)[:, None] * X[pos]
    g[pos, k] = r * r - 1.0
    u = -m[~pos] / s
    ll[~pos] = N.logcdf(u)
    lam = np.exp(N.logpdf(u) - N.logcdf(u))
    g[~pos, :k] = (-lam / s)[:, None] * X[~pos]
    g[~pos, k] = lam * (m[~pos] / s)
    return ll, g


def tobit(a, X):
    """Route 1. Returns (beta, robust covariance of beta, full theta) or None when the fit is not usable."""
    from scipy.optimize import minimize
    k = X.shape[1]
    if a.size <= k + 1 or np.linalg.matrix_rank(X) < k or not (a > 0).any() or (a > 0).all():
        return None
    b0 = np.linalg.lstsq(X, a, rcond=None)[0]
    th0 = np.r_[b0, math.log(max(float(np.std(a - X @ b0)), 1e-3))]
    f = lambda th: -_tobit_parts(th, a, X)[0].sum()
    jac = lambda th: -_tobit_parts(th, a, X)[1].sum(axis=0)
    th = minimize(f, th0, jac=jac, method='BFGS', options={'gtol': 1e-9, 'maxiter': 2000}).x
    eps = 1e-5
    H = np.empty((k + 1, k + 1))
    for i in range(k + 1):
        d = np.zeros(k + 1)
        d[i] = eps
        H[:, i] = (jac(th + d) - jac(th - d)) / (2 * eps)
    try:
        Hi = np.linalg.inv((H + H.T) / 2)
    except np.linalg.LinAlgError:
        return None
    G = _tobit_parts(th, a, X)[1]
    n = a.size
    cov = Hi @ (G.T @ G) @ Hi * (n / (n - k))
    if not np.all(np.isfinite(cov)) or cov[3, 3] <= 0:
        return None
    return th[:k], cov[:k, :k], th


def fit(a, X, cols):
    """Interaction statistic: robust z of the c x log1p(cu$) coefficient; r = z / sqrt(z^2 + df), df = n - k."""
    j = cols.index('cL')
    out = tobit(a, X)
    if out is None:
        return None
    beta, cov, th = out
    n, k = X.shape
    z = float(beta[j]) / math.sqrt(float(cov[j, j]))
    df = n - k
    N = _norm()
    return {'n': n, 'df': df, 'beta': float(beta[j]), 'z': z, 'r': z / math.sqrt(z * z + df),
            'p_pos': float(N.sf(z)), 'p_neg': float(N.cdf(z))}


def stratum_beta(a, X, cols, mask):
    if int(mask.sum()) < MIN_STRATUM_N:
        return None
    keep = [i for i, c in enumerate(cols) if c != 'senate']
    f = fit(a[mask], X[mask][:, keep], [cols[i] for i in keep])
    return None if f is None else f['beta']


def tier(a, X, cols):
    f = fit(a, X, cols)
    if f is None:
        return None, None
    sen = X[:, cols.index('senate')] > 0.5
    bh, bs = stratum_beta(a, X, cols, ~sen), stratum_beta(a, X, cols, sen)
    f['beta_house'], f['beta_senate'] = bh, bs
    if f['r'] >= 0.10 and f['p_pos'] < 0.05 and bh is not None and bs is not None and bh > 0 and bs > 0:
        return 'T1', f
    if f['r'] <= -0.10 and f['p_neg'] < 0.05:
        return 'T4', f
    if f['r'] >= 0.05:
        return 'T2', f
    return 'T3', f


def rederive(a, X, cols):
    """Route 2 (independent): statsmodels GenericLikelihoodModel, Newton from OLS starts, its own numeric
    score_obs/Hessian; HC0 sandwich rescaled by n/(n-k)."""
    from statsmodels.base.model import GenericLikelihoodModel
    N = _norm()
    k = X.shape[1]

    class _Tobit(GenericLikelihoodModel):
        def loglikeobs(self, params):
            b, s = params[:k], math.exp(params[k])
            m = self.exog @ b
            y = self.endog
            return np.where(y > 0, N.logpdf((y - m) / s) - math.log(s), N.logcdf(-m / s))

    b0 = np.linalg.lstsq(X, a, rcond=None)[0]
    start = np.r_[b0, math.log(max(float(np.std(a - X @ b0)), 1e-3))]
    mod = _Tobit(a, X, extra_params_names=['log_sigma'])
    params = mod.fit(start_params=start, method='newton', maxiter=200, disp=0).params
    S = mod.score_obs(params)
    Hi = np.linalg.inv(-mod.hessian(params))
    n = a.size
    cov = Hi @ (S.T @ S) @ Hi * (n / (n - k))
    j = cols.index('cL')
    z = float(params[j]) / math.sqrt(float(cov[j, j]))
    return {'beta': float(params[j]), 'z': z, 'r': z / math.sqrt(z * z + (n - k))}


def spearman_secondary(a, X, cols):
    """Firewalled secondary: partial Spearman (Pearson of rank residuals) of asinh(y) with cL given the rest."""
    from scipy.stats import rankdata
    j = cols.index('cL')
    Z = np.delete(X, j, axis=1)
    ry, rx = _resid(rankdata(a), Z), _resid(rankdata(X[:, j]), Z)
    return float(ry @ rx) / math.sqrt(float(ry @ ry) * float(rx @ rx))


# ---------------------------------------------------------------- design and battery

def design(c, L, a0, partyR, senate):
    c, L = np.asarray(c, float), np.asarray(L, float)
    return np.column_stack([np.ones_like(c), c, L, c * L, np.asarray(a0, float),
                            np.asarray(partyR, float), np.asarray(senate, float)])


def _z(v):
    s = v.std()
    return (v - v.mean()) / s if s > 0 else v * 0.0


def _censor(latent, pi):
    """Zero for the (1-pi) lowest values, value-minus-threshold above (a floor at zero)."""
    q = np.quantile(latent, 1.0 - pi)
    return np.maximum(latent - q, 0.0)


def _latent(rng, X, rho_int, confound):
    Z0 = X[:, [0, 1, 2, 4, 5, 6]]
    lat = 0.5 * _z(X[:, 4]) + 0.2 * _z(X[:, 5])
    if rho_int:
        lat = lat + (rho_int / math.sqrt(1 - rho_int ** 2)) * _z(_resid(X[:, 3], Z0))
    if confound:   # main effects of c and money plus curvature in c; no latent interaction
        lat = lat + 0.45 * _z(X[:, 1]) + 0.45 * _z(X[:, 2]) + 0.30 * _z(X[:, 1] ** 2)
    return lat + rng.standard_normal(X.shape[0])


def _family_F1(rng, lat, pi):
    """F1 censored latent: asinh(y) = max(latent - q, 0)."""
    return _censor(lat, pi)


def _family_F2(rng, lat, pi):
    """F2 Poisson-lognormal count: y = 0.5 x Poisson(exp(alpha + latent/sd)), alpha bisected to share(y>0) ~ pi."""
    s = lat / lat.std()
    lo, hi = -12.0, 6.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if np.mean(1 - np.exp(-np.exp(mid + s))) < pi:
            lo = mid
        else:
            hi = mid
    return np.arcsinh(0.5 * rng.poisson(np.exp((lo + hi) / 2 + s)))


FAMILIES = {'F1': _family_F1, 'F2': _family_F2}
CHECKS = {'a_planted': (RHO_PLANT, False), 'b_null': (0.0, False), 'c_confound': (0.0, True),
          'd_true_confound': (RHO_PLANT, True)}


def _check_pass(name, fam, share, med):
    if name == 'a_planted':
        return True if fam == 'F2' else (share['T1'] >= BAT['a_min_t1'] and med >= BAT['a_min_median_r'])
    if name == 'b_null':
        return share['T1'] <= BAT['b_max_t1'] and share['T4'] <= BAT['b_max_t4']
    if name == 'c_confound':
        return share['T1'] <= BAT['c_max_t1'] and share['T4'] <= BAT['c_max_t4']
    return True if fam == 'F2' else share['T1'] >= BAT['d_min_t1']


def battery(X, pi, a_real=None, reps=REPS):
    """Pinned checks x families. F2 recovery cells are reported (honest power), never gating; every false-positive
    cell gates in both families. a_real (look time) adds the real-outcome null with c permuted (seed 535353)."""
    rows, ok = {}, True
    cells = [(n, f) for n in CHECKS for f in FAMILIES]
    if a_real is not None:
        cells.append(('b_null', 'REAL'))
    for name, fam in cells:
        rng = np.random.default_rng(SEEDS[name])
        tiers, rs = [], []
        for _ in range(reps):
            if fam == 'REAL':
                perm = rng.permutation(X.shape[0])
                Xp = X.copy()
                Xp[:, 1] = X[perm, 1]
                Xp[:, 3] = Xp[:, 1] * Xp[:, 2]
                t, f = tier(a_real, Xp, COLS)
            else:
                rho, conf = CHECKS[name]
                t, f = tier(FAMILIES[fam](rng, _latent(rng, X, rho, conf), pi), X, COLS)
            tiers.append(t)
            rs.append(f['r'] if f else float('nan'))
        share = {k: tiers.count(k) / reps for k in ('T1', 'T2', 'T3', 'T4')}
        share['fail'] = tiers.count(None) / reps
        med = float(np.nanmedian(rs))
        passed = share['fail'] <= BAT['max_fit_fail'] and _check_pass(name, fam, share, med)
        rows['%s/%s' % (name, fam)] = {'seed': SEEDS[name], 'share': share, 'median_r': med, 'pass': passed,
                                        'gating': not (fam == 'F2' and name in ('a_planted', 'd_true_confound'))}
        ok = ok and passed
    return ok, rows


def print_battery(label, n, pi, ok, rows):
    print('  battery [%s] n=%d nonzero-share=%.3f reps=%d -> %s' % (label, n, pi, REPS, 'PASS' if ok else 'FAIL'))
    for name, r in rows.items():
        s = r['share']
        print('    %-20s seed %d  T1 %.3f  T2 %.3f  T3 %.3f  T4 %.3f  fit-fail %.3f  median r %+.4f  %s'
              % (name, r['seed'], s['T1'], s['T2'], s['T3'], s['T4'], s['fail'], r['median_r'],
                 ('pass' if r['pass'] else 'FAIL') if r['gating'] else 'reported (power, not gating)'))


# ---------------------------------------------------------------- inputs

def load_c():
    with open(C_FROZEN, encoding='utf-8') as fh:
        doc = json.load(fh)
    out = {}
    for rec in doc['members'].values():
        if rec['state'] in DELEGATE_STATES:
            continue
        out[rec['bioguide']] = {'c': float(rec['c']), 'partyR': 1.0 if rec['oriented_party'] == 'R' else 0.0,
                                'senate': 1.0 if rec['chamber'] == 'Senate' else 0.0, 'chamber': rec['chamber']}
    return out


def simulate():
    if sha_raw(STANDIN_MONEY) != PIN_STANDIN_RAW:
        print('!! SIMULATE REFUSED: lock-time money stand-in drifted from its pin (the lock-time battery is single-vintage)')
        return False
    cmap = load_c()
    with open(STANDIN_MONEY, encoding='utf-8') as fh:
        money = {m['bioguide']: float(m.get('cu') or 0.0) for m in json.load(fh)}
    ids = sorted(b for b in cmap if b in money)
    rng = np.random.default_rng(LOCK_SUBSAMPLE_SEED)
    a0_all = _censor(rng.standard_normal(len(ids)), 0.64)     # synthetic baseline (64% nonzero: 119th-Congress probe)
    verdict = None
    for n in (450, 300):
        idx = np.sort(rng.choice(len(ids), size=n, replace=False))
        sel = [ids[i] for i in idx]
        X = design([cmap[b]['c'] for b in sel], [math.log1p(money[b]) for b in sel], a0_all[idx],
                   [cmap[b]['partyR'] for b in sel], [cmap[b]['senate'] for b in sel])
        ok, rows = battery(X, LOCK_PI)
        print_battery('lock-time, frozen c + stand-in money, synthetic y/y0', n, LOCK_PI, ok, rows)
        verdict = ok if verdict is None else verdict
    print('  lock-time battery verdict (binds at n=450, the expected panel; n=300 is reported): %s'
          % ('PASS' if verdict else 'FAIL'))
    return verdict


def secondary_fit(a, X):
    f = fit(a, X, COLS)
    return 'n/a' if f is None else 'r %+.4f z %+.3f' % (f['r'], f['z'])


def look():
    with open(OUTCOME, encoding='utf-8') as fh:
        out = json.load(fh)
    if out.get('schema') != 'compet-reciprocity-outcome-v1':
        print('!! DEFECT HALT (not consumed): outcome artifact schema')
        return
    panel = out['members']
    nz = sum(1 for v in panel.values() if v['y'] > 0) / max(len(panel), 1)
    print('  outcome alone: panel %d, share with y>0 %.4f' % (len(panel), nz))
    if nz < MIN_NONZERO:
        print('  VOID-FOR-DEGENERACY (look NOT consumed): share with any CU-direct edge < %.2f' % MIN_NONZERO)
        return
    with open(MONEY, encoding='utf-8') as fh:
        mon = json.load(fh)
    if mon.get('schema') != 'compet-reciprocity-money-v1' or mon.get('cycle') != 2026:
        print('!! DEFECT HALT (not consumed): money artifact schema/cycle')
        return
    verified = {b for b, v in mon['members'].items() if v.get('status') == 'VERIFIED_OPENFEC_CURRENT_CYCLE'}
    vshare = len(verified & set(panel)) / max(len(panel), 1)
    ids = sorted(set(panel) & verified)
    cu_nz = sum(1 for b in ids if mon['members'][b]['cu'] > 0) / max(len(ids), 1)
    print('  money alone: verified share of panel %.4f, n %d, share with cu$>0 %.4f' % (vshare, len(ids), cu_nz))
    if vshare < MIN_MONEY_VERIFIED or len(ids) < MIN_N:
        print('  VOID-FOR-COVERAGE (look NOT consumed): verified money < %.2f of panel or n < %d'
              % (MIN_MONEY_VERIFIED, MIN_N))
        return
    if cu_nz < MIN_NONZERO:
        print('  VOID-FOR-DEGENERACY (look NOT consumed): share with cu$>0 < %.2f' % MIN_NONZERO)
        return
    cmap = load_c()                                     # c is opened only now
    ids = [b for b in ids if b in cmap and panel[b]['chamber_120'] == cmap[b]['chamber']]
    if len(ids) < MIN_N:
        print('  VOID-FOR-COVERAGE (look NOT consumed): n=%d after the c join' % len(ids))
        return
    a = np.array([math.asinh(panel[b]['y']) for b in ids])
    cols = ([cmap[b]['c'] for b in ids], [math.log1p(mon['members'][b]['cu']) for b in ids],
            [math.asinh(panel[b]['y0']) for b in ids], [cmap[b]['partyR'] for b in ids],
            [cmap[b]['senate'] for b in ids])
    X = design(*cols)
    pi = float(np.mean(a > 0))
    ok, rows = battery(X, pi, a_real=a)
    print_battery('look-time, real design', len(ids), pi, ok, rows)
    if not ok:
        print('  LOOK REFUSED (not consumed): simulate battery failure -> RETIRED WITHOUT A LOOK')
        return
    t, f = tier(a, X, COLS)
    r2 = rederive(a, X, COLS) if f is not None else None
    if f is None or abs(r2['beta'] - f['beta']) > 1e-4 * max(1.0, abs(f['beta'])) or abs(r2['z'] - f['z']) > 1e-3:
        print('!! DEFECT HALT (nothing recorded; not consumed): route 1 and route 2 disagree', f, r2)
        return
    print('\n## RESULT-R (append verbatim)')
    print('n=%d df=%d Tobit c x log1p(cu$): beta %+.6f  robust z %+.4f  r %+.6f  p_pos %.6f  p_neg %.6f'
          % (f['n'], f['df'], f['beta'], f['z'], f['r'], f['p_pos'], f['p_neg']))
    print('chamber strata beta: House %s  Senate %s' % (f['beta_house'], f['beta_senate']))
    print('route 2 (statsmodels GenericLikelihoodModel): beta %+.6f  z %+.4f  r %+.6f' % (r2['beta'], r2['z'], r2['r']))
    print('TIER %s: "%s"' % (t, WORDS[t]))
    print('secondaries (firewalled; ground nothing):')
    print('  partial Spearman (rank residuals) %+.4f' % spearman_secondary(a, X, COLS))
    ind = [math.log1p(mon['members'][b].get('ind') or 0.0) for b in ids]
    print('  industry money leg in place of cu$: %s' % secondary_fit(a, design(cols[0], ind, *cols[2:])))
    mg = [mon['members'][b].get('margin_2026') for b in ids]
    keep = [i for i, v in enumerate(mg) if v is not None]
    if len(keep) >= MIN_N:
        cm = [1 + math.exp(-max(float(mg[i]), 0.0) / 5.0) for i in keep]
        sub = [[col[i] for i in keep] for col in cols]
        print('  realized 2026 margin in place of c (n=%d): %s' % (len(keep), secondary_fit(a[keep], design(cm, *sub[1:]))))
    else:
        print('  realized 2026 margin in place of c: n=%d < %d, not computed' % (len(keep), MIN_N))
    print('\nAppend the RESULT-R section to compet_reciprocity_prereg.md verbatim and commit same session.')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--simulate', action='store_true')
    ap.add_argument('--look', action='store_true')
    args = ap.parse_args(argv)
    print('CM-1 competitiveness reciprocity corroboration look (prereg: compet_reciprocity_prereg.md)')
    text = open(PREREG, encoding='utf-8').read() if os.path.exists(PREREG) else ''
    fp = fingerprints()
    print('  lock: %s | look: %s | input fingerprints: %s | collection record: %s'
          % (is_locked(text),'TAKEN' if '\n## RESULT-R' in text else 'not yet taken',
             'MATCH' if fingerprints_ok(fp) else 'DRIFT', 'present' if collection_record(text) else 'absent'))
    if args.look:
        refusal = gate(text, fp, dt.date.today())
        if refusal:
            print(refusal)
            return
        look()
    elif args.simulate:
        simulate()


if __name__ == '__main__':
    main()
