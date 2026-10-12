# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Text-C CONFIRMATORY (big5_textC_prereg.md @9f714ca). Full roster: Empath text -> behavioral
diligence_z, held-out CV, exact perm-p, partial-r controlling party+age. ONE LOOK. Confirms the N=90
lead generalizes and is confound-independent."""
import json, sqlite3, csv
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
from empath import Empath
from scipy import stats
sys = _s; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import moneyball_core as MC
B = MB_ROOT
BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
P = MC.party()
b2n = MC.bio2name(); n2b = {v: k for k, v in b2n.items()}
# age from legislators.csv (bioguide -> birth year); the available seniority proxy
byr = {}
with open(B + r'\fec\legislators.csv', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        bd = row.get('birthday') or ''
        if len(bd) >= 4 and bd[:4].isdigit(): byr[row.get('bioguide_id')] = int(bd[:4])
con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
TXT = {m: (t or '') for m, t in con.execute("select member, group_concat(text,' ') from speeches group by member")}
con.close()
MEM = [m for m in BD if (BD[m] or {}).get('diligence_z') is not None
       and len(TXT.get(m, '')) > 2000 and P.get(m) in ('D', 'R')]
lex = Empath(); CATS = sorted((lex.analyze('a b c', normalize=True) or {}).keys())
L = np.array([[(lex.analyze(TXT[m][:500000], normalize=True) or {}).get(c, 0.0) for c in CATS] for m in MEM])
lmu = L.mean(0); lsd = L.std(0, ddof=1); lsd[lsd < 1e-12] = 1; L = (L - lmu) / lsd
yC = np.array([BD[m]['diligence_z'] for m in MEM])
party = np.array([1.0 if P.get(m) == 'R' else 0.0 for m in MEM])
ages = [byr.get(n2b.get(m)) for m in MEM]
med = np.median([a for a in ages if a]); age = np.array([(2026 - (a or (2026 - med))) for a in ages], float)
print('CONFIRMATORY N=%d | Empath %d cats | age known %d/%d' % (len(MEM), L.shape[1], sum(1 for a in ages if a), len(MEM)))
ALPHAS = [1, 3, 10, 30, 100, 300, 1000]; SEEDS = [7, 17, 29, 43, 61]
def hr(X, y):
    rs = []
    for s in SEEDS:
        for tr, te in KFold(5, shuffle=True, random_state=s).split(X):
            g = GridSearchCV(Ridge(), {'alpha': ALPHAS}, cv=3, scoring='neg_mean_squared_error'); g.fit(X[tr], y[tr]); p = g.predict(X[te])
            rs.append(0.0 if p.std() < 1e-9 else float(np.corrcoef(p, y[te])[0, 1]))
    return float(np.mean(rs))
def cvpred(X, y):
    Pr = np.zeros((len(SEEDS), len(y)))
    for i, s in enumerate(SEEDS):
        for tr, te in KFold(5, shuffle=True, random_state=s).split(X):
            g = GridSearchCV(Ridge(), {'alpha': ALPHAS}, cv=3, scoring='neg_mean_squared_error'); g.fit(X[tr], y[tr]); Pr[i, te] = g.predict(X[te])
    return Pr.mean(0)
def resid(v, Z):
    Z1 = np.column_stack([np.ones(len(v)), Z]); b, *_ = np.linalg.lstsq(Z1, v, rcond=None); return v - Z1 @ b
obs = hr(L, yC)
rng = np.random.RandomState(1); perms = [hr(L, rng.permutation(yC)) for _ in range(300)]
permp = float((np.sum(np.array(perms) >= obs) + 1) / 301)
pred = cvpred(L, yC)
Zc = np.column_stack([party, age])
pr = resid(pred, Zc); yr = resid(yC, Zc)
r_partial = float(np.corrcoef(pr, yr)[0, 1]); n = len(yC)
t = r_partial * np.sqrt((n - 4) / max(1e-9, 1 - r_partial ** 2)); p_partial = float(stats.t.sf(abs(t), n - 4))
r_partyalone = float(np.corrcoef(party, yC)[0, 1])
wd = float(np.corrcoef(pred[party == 0], yC[party == 0])[0, 1]); wr = float(np.corrcoef(pred[party == 1], yC[party == 1])[0, 1])
print('\n=== TEXT-C CONFIRMATORY RESULT ===')
print('  held-out r (Empath -> diligence) = %+.3f   exact perm-p = %.4f' % (obs, permp))
print('  party alone -> diligence         = %+.3f' % r_partyalone)
print('  PARTIAL r | party+age            = %+.3f   p = %.5f   (%.0f%% of raw)' % (r_partial, p_partial, 100 * r_partial / obs if obs else 0))
print('  within-D (n=%d) = %+.3f | within-R (n=%d) = %+.3f' % (int((party == 0).sum()), wd, int((party == 1).sum()), wr))
c1 = obs >= 0.20 and permp < 0.05
c2 = r_partial >= 0.15 and p_partial < 0.05 and r_partial >= 0.6 * obs
c3 = wd > 0 and wr > 0
print('\n  BAR C1 held-out r>=+0.20 & perm-p<.05:        %s' % ('CLEAR' if c1 else 'MISS'))
print('  BAR C2 partial>=+0.15, p<.05, >=0.6x raw:      %s' % ('CLEAR' if c2 else 'MISS'))
print('  BAR C3 within-party both > 0:                 %s' % ('CLEAR' if c3 else 'MISS'))
print('\nVERDICT: %s' % ('PASS — text measures C CONFIRMED on the full roster (confound-independent)' if (c1 and c2 and c3) else 'MISS — lead does not generalize; C stays behavioral-only'))
