# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""FAITHFUL Marrero-style replication (big5_audio_faithful_prereg.md @223f11d). Predict PERSONALITY
(Pink) from a VOCAL block (eGeMAPS 88) + a LINGUISTIC block (Empath) + their JOINT model; report the
vocal/linguistic/joint variance decomposition (Marrero 3.13/6.24/10.67 analog). ONE LOOK.
- Bar 1a: a-priori acoustic-E composite -> Pink-E (powered primary).  Bar 1b: full-88 ridge -> Pink-E.
- Bar 2: super-additivity joint-E > ling-E (caveat: linguistic shares source with Pink).
- Bar 3: vocal A-ES separation < .45 with vocal-A validity gate (the halo-crack / CT-CM indicator)."""
import json, sqlite3
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
from scipy import stats
from empath import Empath
sys = _s; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
AU = json.load(open(B + r'\data\members\b5_audio_cache.json', encoding='utf-8'))
MEM = AU['members']; FN = AU['feature_names']
V = np.array([AU['z'][m] for m in MEM])                       # vocal 88 (already z-within-source)
PINK = json.load(open(B + r'\data\members\big5_llm_opus.json', encoding='utf-8'))['traits']
def pink_t(t):
    return np.array([(PINK[m]['raw_z'][t] if isinstance(PINK[m].get('raw_z'), dict) else PINK[m][t]) for m in MEM])

# linguistic block: Empath category rates on each member's speech text
lex = Empath(); CATS = sorted((lex.analyze('the quick brown fox jumps', normalize=True) or {}).keys())
con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
TXT = {m: (t or '') for m, t in con.execute("select member, group_concat(text,' ') from speeches group by member")}
con.close()
L = np.array([[(lex.analyze(TXT.get(m, '')[:500000], normalize=True) or {}).get(c, 0.0) for c in CATS] for m in MEM])
lmu = L.mean(0); lsd = L.std(0, ddof=1); lsd[lsd < 1e-12] = 1; L = (L - lmu) / lsd
J = np.hstack([V, L])
print('FAITHFUL replication N=%d | blocks: vocal %d, linguistic %d, joint %d' % (len(MEM), V.shape[1], L.shape[1], J.shape[1]))

ALPHAS = [1, 3, 10, 30, 100, 300, 1000]; SEEDS = [7, 17, 29, 43, 61]
def hr(X, y):
    rs = []
    for s in SEEDS:
        for tr, te in KFold(5, shuffle=True, random_state=s).split(X):
            g = GridSearchCV(Ridge(), {'alpha': ALPHAS}, cv=3, scoring='neg_mean_squared_error'); g.fit(X[tr], y[tr]); p = g.predict(X[te])
            rs.append(0.0 if p.std() < 1e-9 else float(np.corrcoef(p, y[te])[0, 1]))
    return float(np.mean(rs))
def cvpred(X, y):
    P = np.zeros((len(SEEDS), len(y)))
    for i, s in enumerate(SEEDS):
        for tr, te in KFold(5, shuffle=True, random_state=s).split(X):
            g = GridSearchCV(Ridge(), {'alpha': ALPHAS}, cv=3, scoring='neg_mean_squared_error'); g.fit(X[tr], y[tr]); P[i, te] = g.predict(X[te])
    return P.mean(0)
def pnull(X, y, K=100):
    r = np.random.RandomState(0); return float(np.percentile([hr(X, r.permutation(y)) for _ in range(K)], 95))

TR = ['E', 'A', 'C', 'O', 'ES']
print('\n=== variance decomposition (held-out r; r^2) — vocal / linguistic / joint ===')
dec = {}
for t in TR:
    y = pink_t(t); rv = hr(V, y); rl = hr(L, y); rj = hr(J, y); dec[t] = (rv, rl, rj)
    print('  %-3s vocal %+.3f (r2 %.3f) | ling %+.3f (r2 %.3f) | joint %+.3f | vocal-adds-over-ling %+.3f'
          % (t, rv, max(rv, 0) ** 2, rl, max(rl, 0) ** 2, rj, rj - rl))

EMARK = ['loudness_sma3_amean', 'loudness_sma3_stddevNorm', 'F0semitoneFrom27.5Hz_sma3nz_stddevNorm',
         'spectralFlux_sma3_amean', 'loudnessPeaksPerSec', 'VoicedSegmentsPerSec']
idx = [FN.index(f) for f in EMARK]
Eidx = V[:, idx].mean(1)                                       # a-priori composite (already z, all + for E)
yE = pink_t('E')
r_idx = float(np.corrcoef(Eidx, yE)[0, 1])
tval = r_idx * np.sqrt((len(MEM) - 2) / max(1e-9, 1 - r_idx ** 2)); p1 = float(stats.t.sf(tval, len(MEM) - 2))
b1a = r_idx >= 0.15 and p1 < 0.05
print('\nBAR 1a (a-priori acoustic-E index -> Pink-E): r=%+.3f  (n=%d, one-sided p=%.4f) -> %s'
      % (r_idx, len(MEM), p1, 'CLEAR' if b1a else 'MISS'))
nullE = pnull(V, yE); b1b = dec['E'][0] >= 0.15 and dec['E'][0] > nullE
print('BAR 1b (full eGeMAPS-88 -> Pink-E): held-out r %+.3f  (> perm-null %+.3f) -> %s'
      % (dec['E'][0], nullE, 'CLEAR' if b1b else 'MISS'))
add = dec['E'][2] - dec['E'][1]; b2 = add >= 0.03
print('BAR 2 (super-additivity, caveated): joint-E %+.3f - ling-E %+.3f = %+.3f -> %s [ling shares source w/ Pink]'
      % (dec['E'][2], dec['E'][1], add, 'CLEAR' if b2 else 'MISS'))
vA = cvpred(V, pink_t('A')); vES = cvpred(V, pink_t('ES')); aes = abs(float(np.corrcoef(vA, vES)[0, 1]))
nullA = pnull(V, pink_t('A')); a_valid = dec['A'][0] > nullA; b3 = aes < 0.45 and a_valid
print('BAR 3 (halo): vocal A-ES |r| %.3f  (<0.45; Pink halo .65) | vocal-A valid=%s (%.3f vs null %.3f) -> %s'
      % (aes, a_valid, dec['A'][0], nullA, 'CLEAR' if b3 else 'MISS'))
print('\nVERDICT: Bar1a %s | Bar1b %s | Bar2 %s | Bar3 %s'
      % tuple('PASS' if b else 'MISS' for b in [b1a, b1b, b2, b3]))
