# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""CTCM2 — Pink A-ES halo latent overlay with the EMBEDDING Blue as the identifying 3rd indicator
(big5_round4_prereg.md @d59bde5). The tonight-earlier CT-CM was under-identified because lexical
Blue's A/C/O were collinear; the embedding Blue (b5_round4_eval winner) provides the independent
indicator the model needs. Instruments' own scores are UNTOUCHED (independence intact); the latent
is a THIRD overlay artifact. semopy CT-CM: trait =~ pink(raw_z)+blue_emb+behavioral, orthogonal
PINK METHOD factor absorbs the judge halo.
Bars (ONE look): latent A-ES <=0.50, latent C vs votes >=+0.15, latent discriminant <=0.30,
no Heywood, positive on-trait loadings. PASS -> write big5_latent.json. Usage: python fec/members/big5_ctcm2.py"""
import json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd, semopy
import members.b5_round4_eval as R4
B = MB_ROOT
TR = ['O', 'C', 'E', 'A', 'ES']; BEH = {'A': 'cooperation_z', 'C': 'diligence_z', 'E': 'visibility_z', 'O': 'breadth_z'}

# --- winner Blue = embedding Blue full-roster scores (deterministic selection on SELECT half) ---
winner = R4.selection_search()
Z = R4.Zc(winner); ho, models, _ = R4.fit_models(winner, Z, set(R4.MEMBERS))
Xm, keep = R4.member_matrix(winner, Z, set(R4.MEMBERS)); midx = {m: i for i, m in enumerate(keep)}
blue = {}
for t in ('A', 'C', 'E', 'O'):
    pred = models[t].predict(Xm); mu, sd = pred.mean(), pred.std() or 1
    for i, m in enumerate(keep): blue.setdefault(m, {})[t] = float((pred[i] - mu) / sd)
print('embedding-Blue winner=%s (A/C/E/O held-out %s)' % (winner, {t: round(ho[t], 3) for t in ('A', 'C', 'E', 'O')}))

PINK = json.load(open(B + r'\data\members\big5_llm_opus.json', encoding='utf-8'))['traits']
BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
PAN = json.load(open(B + r'\behavior_panel.json', encoding='utf-8'))['members']
rows = []
for m in PINK:
    if m not in blue: continue
    pz = PINK[m].get('raw_z') if isinstance(PINK[m].get('raw_z'), dict) else {t: PINK[m][t] for t in TR}
    r = {'member': m}
    for t in TR: r['pink_' + t] = pz[t]
    for t in ('A', 'C', 'E', 'O'): r['blue_' + t] = blue[m][t]
    ok = all(BD.get(m, {}).get(k) is not None for k in BEH.values())
    for t, k in BEH.items(): r['behav_' + t] = BD.get(m, {}).get(k)
    if ok: rows.append(r)
df = pd.DataFrame(rows).set_index('member')
for c in df.columns: df[c] = (df[c] - df[c].mean()) / df[c].std(ddof=1)
print('CTCM2 complete-case members: %d' % len(df))

DESC = """
F_O =~ pink_O + blue_O + behav_O
F_C =~ pink_C + blue_C + behav_C
F_E =~ pink_E + blue_E + behav_E
F_A =~ pink_A + blue_A + behav_A
F_ES =~ pink_ES + behav_ES_placeholder
M =~ pink_O + pink_C + pink_E + pink_A + pink_ES
M ~~ 0*F_O
M ~~ 0*F_C
M ~~ 0*F_E
M ~~ 0*F_A
M ~~ 0*F_ES
"""
# ES has no blue_emb and no behavioral criterion -> 2 indicators need a fixed-variance anchor.
# Use pink_ES + a behavioral proxy: dominance_z (steadiness under pressure, weak but present) as ES2.
df['behav_ES_placeholder'] = ((lambda s: (s - s.mean()) / (s.std(ddof=1) or 1))(
    pd.Series({m: -(BD.get(m, {}).get('dominance_z') or 0.0) for m in df.index})))
model = semopy.Model(DESC)
try:
    model.fit(df)
except Exception as e:
    print('fit error:', str(e)[:120])
ins = model.inspect()
heywood = any(r['op'] == '~~' and r['lval'] == r['rval'] and float(r['Estimate']) < -1e-6 for _, r in ins.iterrows())
neg_load = []
for _, r in ins.iterrows():
    if r['op'] == '~' and str(r['rval']).startswith('F_') and float(r['Estimate']) < 0: neg_load.append(r['lval'])
try:
    scores = model.predict_factors(df); scores.index = df.index
    lat = {t: scores['F_' + t] for t in TR}
    def pear(a, b):
        a, b = np.asarray(a, float), np.asarray(b, float)
        return float(np.corrcoef(a, b)[0, 1]) if a.std() and b.std() else 0.0
    aes = abs(pear(lat['A'], lat['ES']))
    offs = [abs(pear(lat[a], lat[b])) for i, a in enumerate(TR) for b in TR[i + 1:]]; disc = sum(offs) / len(offs)
    vc = [(lat['C'][m], PAN[m]['votes_cast']) for m in df.index if PAN.get(m, {}).get('votes_cast') is not None]
    def spear(p):
        def rk(v):
            o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
            while i < len(o):
                j = i
                while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]: j += 1
                for k in range(i, j + 1): r[o[k]] = (i + j) / 2
                i = j + 1
            return r
        return pear(rk([x[0] for x in p]), rk([x[1] for x in p]))
    rC = spear(vc)
    conv = True
except Exception as e:
    print('predict/eval error:', str(e)[:120]); aes = disc = rC = 9; conv = False
print('\n===== CTCM2 latent results (n=%d) =====' % len(df))
print('  latent A-ES |r| = %.3f | discriminant mean = %.3f | C vs votes = %+.3f' % (aes, disc, rC))
print('  Heywood: %s | negative on-trait loadings: %s | converged: %s' % (heywood, neg_load or 'none', conv))
bars = [('latent A-ES <= 0.50', aes <= 0.50, '%.3f' % aes),
        ('latent C vs votes >= +0.15', rC >= 0.15, '%+.3f' % rC),
        ('latent discriminant <= 0.30', disc <= 0.30, '%.3f' % disc),
        ('no Heywood, loadings positive, converged', (not heywood) and not neg_load and conv, 'ok' if not (heywood or neg_load) else str(neg_load))]
for nm, hit, det in bars: print('  BAR %-32s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', det))
ok = all(b[1] for b in bars)
if ok and '--go' in sys.argv:
    from moneyball_core import dump_atomic
    dump_atomic({'members': {m: {t: round(float(lat[t][m]), 3) for t in TR} for m in df.index},
                 'method': 'CT-CM latent (semopy): pink(raw_z)+embedding-blue+behavioral; orthogonal pink METHOD '
                           'factor absorbs judge halo. round-4 prereg @d59bde5.', 'n': len(df),
                 'latent_a_es': round(aes, 3), 'latent_disc': round(disc, 3), 'latent_c_votes': round(rC, 3),
                 'blue_base': winner}, B + r'\big5_latent.json')
    print('CTCM2 VERDICT: PASS — big5_latent.json written')
else:
    print('CTCM2 VERDICT: %s' % ('PASS (rerun with --go to ship)' if ok else 'MISS — documented; no overlay shipped'))
