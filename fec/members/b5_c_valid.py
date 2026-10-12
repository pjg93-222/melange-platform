# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Round-2 TEST A (big5_audio_round2_prereg.md @7f41388): nail down C against the VALID criterion.
Vocal (eGeMAPS-88) + linguistic (Empath) -> behavioral diligence_z (the only valid C measure; text-C
against votes is the 5-method null). Both blocks are CLEAN here (neither shares method with behavior).
Marrero/Rubio got C from voice against valid criteria; this is our valid-criterion C test. ONE LOOK."""
import json, sqlite3
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
from empath import Empath
sys = _s; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
AU = json.load(open(B + r'\data\members\b5_audio_cache.json', encoding='utf-8'))
MEM = AU['members']
V = np.array([AU['z'][m] for m in MEM])
BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
TGT = {'C': 'diligence_z', 'A': 'cooperation_z', 'E': 'visibility_z', 'O': 'breadth_z'}   # behavioral analogs; no behavioral ES
def yb(t):
    return np.array([(BD.get(m) or {}).get(TGT[t], np.nan) for m in MEM])
lex = Empath(); CATS = sorted((lex.analyze('the quick brown fox', normalize=True) or {}).keys())
con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
TXT = {m: (t or '') for m, t in con.execute("select member, group_concat(text,' ') from speeches group by member")}
con.close()
L = np.array([[(lex.analyze(TXT.get(m, '')[:500000], normalize=True) or {}).get(c, 0.0) for c in CATS] for m in MEM])
lmu = L.mean(0); lsd = L.std(0, ddof=1); lsd[lsd < 1e-12] = 1; L = (L - lmu) / lsd
J = np.hstack([V, L])
ALPHAS = [1, 3, 10, 30, 100, 300, 1000]; SEEDS = [7, 17, 29, 43, 61]
def hr(X, y):
    rs = []
    for s in SEEDS:
        for tr, te in KFold(5, shuffle=True, random_state=s).split(X):
            g = GridSearchCV(Ridge(), {'alpha': ALPHAS}, cv=3, scoring='neg_mean_squared_error'); g.fit(X[tr], y[tr]); p = g.predict(X[te])
            rs.append(0.0 if p.std() < 1e-9 else float(np.corrcoef(p, y[te])[0, 1]))
    return float(np.mean(rs))
def permp(X, y, obs, K=300):
    r = np.random.RandomState(1); perms = [hr(X, r.permutation(y)) for _ in range(K)]
    return float((np.sum(np.array(perms) >= obs) + 1) / (K + 1))

print('Round-2 TEST A — voice/text -> behavioral (valid) criteria, N=%d' % len(MEM))
print('=== decomposition: vocal / linguistic / joint (held-out r) ===')
dec = {}
for t in ['C', 'A', 'E', 'O']:
    y = yb(t); ok = ~np.isnan(y)
    rv = hr(V[ok], y[ok]); rl = hr(L[ok], y[ok]); rj = hr(J[ok], y[ok]); dec[t] = (rv, rl, rj, int(ok.sum()))
    print('  %s (%s, n=%d): vocal %+.3f | ling %+.3f | joint %+.3f | vocal-adds %+.3f'
          % (t, TGT[t], int(ok.sum()), rv, rl, rj, rj - rl))
# BAR A1/A2 on C (diligence)
yC = yb('C'); ok = ~np.isnan(yC)
rvC = dec['C'][0]; pC = permp(V[ok], yC[ok], rvC)
a1 = rvC >= 0.15 and pC < 0.05
print('\nBAR A1 (vocal -> diligence = does VOICE measure C): r=%+.3f  exact perm-p=%.3f -> %s'
      % (rvC, pC, 'CLEAR' if a1 else 'MISS'))
addC = dec['C'][2] - dec['C'][1]; a2 = addC >= 0.03
print('BAR A2 (super-additivity, CLEAN): joint-C %+.3f - ling-C %+.3f = %+.3f -> %s'
      % (dec['C'][2], dec['C'][1], addC, 'CLEAR' if a2 else 'MISS'))
# also: is linguistic (text) null on C against the valid criterion? (confirms text-C null)
rlC = dec['C'][1]; plC = permp(L[ok], yC[ok], rlC)
print('  [context] linguistic -> diligence r=%+.3f perm-p=%.3f (%s) — text-C against valid criterion'
      % (rlC, plC, 'sig' if plC < 0.05 else 'NULL'))
print('\nVERDICT C: A1 %s | A2 %s' % ('PASS' if a1 else 'MISS', 'PASS' if a2 else 'MISS'))
