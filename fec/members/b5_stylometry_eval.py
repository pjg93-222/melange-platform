# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""B6 — blue rebuilt on a NEW feature basis (stylometry), ONE-look evaluation
(big5_round3_prereg.md @1967b15). Round-1/2 established the 31 category-rate features cannot carry
genre-stable multi-trait structure; B6 changes the BASIS: function-word profiles, punctuation and
sentence-length distributions, hedging/certainty markers, person-reference rates, and type-token
statistics, computed PER GENRE from the raw corpus, standardized WITHIN genre across members
(genre-controlled BY CONSTRUCTION), then token-weighted averaged across genres per member, then
fit to the v2 behavioral targets (same nested CV, same seed, same alpha grid). Blue never touches
pink at any step.
Bars (locked): held-out r A >= +0.14, C >= +0.13, O >= +0.20, E >= +0.20; AND family MTMM mean
same-trait cross-genre > mean cross-trait within-genre. On MISS: v2 stands; further blue work
requires a different modality, not another lexical iteration."""
import json, math, re, sqlite3, collections, sys
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
B = MB_ROOT

FUNC = ['the','of','and','to','in','that','it','is','was','for','on','are','as','with','they','at','be','this','from',
        'or','but','not','what','all','we','you','i','my','our','your','their','he','she','him','her','them','us',
        'would','could','should','will','can','must','if','because','so','however','although']
HEDGE = re.compile(r'\b(maybe|perhaps|somewhat|arguably|i think|i believe|it seems|sort of|kind of|possibly|probably|might)\b', re.I)
CERT = re.compile(r'\b(must|always|never|clearly|certainly|absolutely|definitely|without question|no doubt|undeniabl)\b', re.I)
NUMS = re.compile(r'\b\d[\d,.]*\b')
QRX = re.compile(r'\?')
EXCL = re.compile(r'!')
SENT = re.compile(r'[.!?]+')

def featurize(text):
    words = text.lower().split()
    n = len(words)
    if n < 50: return None
    cnt = collections.Counter(words)
    f = [cnt.get(w, 0) / n for w in FUNC]                       # function-word profile (46)
    sents = [s for s in SENT.split(text) if s.strip()]
    slens = [len(s.split()) for s in sents] or [n]
    f += [sum(slens) / len(slens), (np.std(slens) if len(slens) > 1 else 0.0)]   # sentence length mean/sd
    f += [HEDGE.findall(text).__len__() / n * 100, CERT.findall(text).__len__() / n * 100]
    f += [len(NUMS.findall(text)) / n * 100, QRX.findall(text).__len__() / n * 100,
          EXCL.findall(text).__len__() / n * 100]
    # MATTR-50 type-token
    W = 50
    if n >= W:
        rat = [len(set(words[i:i + W])) / W for i in range(0, n - W, W)]
        f.append(sum(rat) / len(rat))
    else:
        f.append(len(set(words)) / n)
    f.append(sum(len(w) for w in words) / n)                    # mean word length
    return f
NFEAT = len(FUNC) + 9

print('B6 stylometry: extracting per-member per-genre features from the corpus ...')
con = sqlite3.connect(B + r'\social_media.db', timeout=30); con.execute('PRAGMA query_only=1')
GEN = {}
for g in ('CREC', 'INTERVIEW'):
    agg = {}
    for m, txt in con.execute("select member, group_concat(text,' ') from speeches where source=? group by member", (g,)):
        v = featurize(txt or '')
        if v is not None: agg[m] = (np.array(v), len((txt or '').split()))
    GEN[g] = agg
    print('  %s: %d members' % (g, len(agg)))
# social from posts
import moneyball_core as MC
b2n = MC.bio2name()
soc_txt = collections.defaultdict(list)
for bg, txt in con.execute("select bioguide, group_concat(text,' ') from posts where repost_of is null group by bioguide"):
    m = b2n.get(bg)
    if m: soc_txt[m].append(txt or '')
con.close()
agg = {}
for m, ts in soc_txt.items():
    v = featurize(' '.join(ts))
    if v is not None: agg[m] = (np.array(v), len(' '.join(ts).split()))
GEN['social'] = agg
print('  social: %d members' % len(agg))

def zgenre(g):   # standardize each feature WITHIN genre across members
    ms = sorted(GEN[g])
    X = np.array([GEN[g][m][0] for m in ms])
    mu, sd = X.mean(axis=0), X.std(axis=0, ddof=1); sd[sd == 0] = 1
    return ms, (X - mu) / sd, {m: GEN[g][m][1] for m in ms}
ZG = {g: zgenre(g) for g in GEN}

# member-level: token-weighted mean of within-genre-z features
allm = sorted(set().union(*[set(ZG[g][0]) for g in ZG]))
Xm = []; keep = []
for m in allm:
    num = np.zeros(NFEAT); den = 0.0
    for g in ZG:
        ms, Z, wtok = ZG[g]
        if m in ms:
            i = ms.index(m); w = wtok[m]
            num += Z[i] * w; den += w
    if den > 0: Xm.append(num / den); keep.append(m)
Xm = np.array(Xm); midx = {m: i for i, m in enumerate(keep)}
print('member feature matrix: %d x %d' % Xm.shape)

BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
TARGET = {'A': 'cooperation_z', 'C': 'diligence_z', 'E': 'visibility_z', 'O': 'breadth_z'}
BARS = {'A': 0.14, 'C': 0.13, 'E': 0.20, 'O': 0.20}
def heldout_r(Xa, y):
    rs = []
    for tr, te in KFold(5, shuffle=True, random_state=7).split(Xa):
        g = GridSearchCV(Ridge(), {'alpha': [1, 3, 10, 30, 100, 300]}, cv=3, scoring='neg_mean_squared_error')
        g.fit(Xa[tr], y[tr]); p = g.predict(Xa[te])
        rs.append(0.0 if p.std() < 1e-9 else float(np.corrcoef(p, y[te])[0, 1]))
    return float(np.mean(rs))
ho = {}; models = {}
for t, tgt in TARGET.items():
    ms = [m for m in keep if m in BD and BD[m].get(tgt) is not None]
    Xa = Xm[[midx[m] for m in ms]]; y = np.array([BD[m][tgt] for m in ms])
    ho[t] = heldout_r(Xa, y)
    g = GridSearchCV(Ridge(), {'alpha': [1, 3, 10, 30, 100, 300]}, cv=5, scoring='neg_mean_squared_error')
    g.fit(Xa, y); models[t] = g.best_estimator_
    print('  %-2s <- %-14s n=%-3d  STYLOMETRY held-out r=%+.3f  (bar >= %+.2f)' % (t, tgt, len(ms), ho[t], BARS[t]))

# family MTMM: per-genre trait scores = model applied to that genre's within-genre-z features
trs = list(TARGET); pairs = [('CREC', 'INTERVIEW'), ('CREC', 'social'), ('INTERVIEW', 'social')]
def gsc(t, g, m):
    ms, Z, _ = ZG[g]
    if m not in ms: return None
    return float(models[t].predict(Z[ms.index(m)].reshape(1, -1))[0])
same, cross = [], []
for g1, g2 in pairs:
    common = [m for m in keep if m in ZG[g1][0] and m in ZG[g2][0]]
    for t in trs:
        a = np.array([gsc(t, g1, m) for m in common]); b = np.array([gsc(t, g2, m) for m in common])
        if a.std() > 0 and b.std() > 0: same.append(float(np.corrcoef(a, b)[0, 1]))
for g in ZG:
    common = [m for m in keep if m in ZG[g][0]]
    for i, t1 in enumerate(trs):
        for t2 in trs[i + 1:]:
            a = np.array([gsc(t1, g, m) for m in common]); b = np.array([gsc(t2, g, m) for m in common])
            if a.std() > 0 and b.std() > 0: cross.append(abs(float(np.corrcoef(a, b)[0, 1])))
mts, mtc = float(np.mean(same)), float(np.mean(cross))
print('  family MTMM: same-trait cross-genre %.3f vs cross-trait within-genre %.3f' % (mts, mtc))
bars = [(t, ho[t] >= BARS[t]) for t in TARGET] + [('MTMM', mts > mtc)]
for t, h in bars[:-1]: print('  BAR %s held-out >= %+.2f: %s (%+.3f)' % (t, BARS[t], 'CLEAR' if h else 'MISS', ho[t]))
print('  BAR family MTMM same > cross: %s' % ('CLEAR' if mts > mtc else 'MISS'))
ok = all(h for _, h in bars)
print('B6 VERDICT: %s' % ('PASS — stylometric basis replaces v2 (promotion steps follow)'
                          if ok else 'MISS — v2 stands; lexical modality closed (CT-CM capstone is the remaining blue lever)'))
