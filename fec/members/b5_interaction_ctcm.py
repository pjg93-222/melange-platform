# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Halo attack via NEUROTICISM (big5_halo_neuroticism_prereg.md @10728e1). CT-CM with a TONE-FREE
conversational-dynamics anchor: interaction_features (turn-cutoffs, contested bursts, defensiveness,
discourse markers) — structural, not semantic, so NOT collinear with the text-tone that IS the halo.
N composite (high-N=reactive/defensive) -> ES indicator = -(composite). 4th and LAST derived-signal
CT-CM attempt (stopping rule). DISPLAY-ONLY latent overlay; engine untouched. ONE LOOK."""
import json
sys = _s; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd, semopy
B = MB_ROOT
IF = json.load(open(B + r'\data\lobbying\interaction_features.json', encoding='utf-8'))
IFM = IF.get('members') or IF
PINK = json.load(open(B + r'\data\members\big5_llm_opus.json', encoding='utf-8'))['traits']
BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
PAN = json.load(open(B + r'\behavior_panel.json', encoding='utf-8'))['members']
TR = ['O', 'C', 'E', 'A', 'ES']; BEH = {'O': 'breadth_z', 'C': 'diligence_z', 'E': 'visibility_z', 'A': 'cooperation_z'}
NKEYS = ['cutoff', 'burst', 'defense', 'discmark']   # high-N conversational markers
# sample = interaction ∩ Pink ∩ behavioral, with all N markers present
cand = [m for m in IFM if m in PINK and m in BD
        and all((IFM[m] or {}).get(k) is not None for k in NKEYS)
        and all((BD.get(m) or {}).get(BEH[t]) is not None for t in BEH)]
# z the raw N markers across the sample, sum (signed +N), ES anchor = -(N composite)
NM = np.array([[IFM[m][k] for k in NKEYS] for m in cand], float)
NM = (NM - NM.mean(0)) / np.where(NM.std(0, ddof=1) < 1e-12, 1, NM.std(0, ddof=1))
Ncomp = NM.sum(1); interES = -Ncomp   # ES = -N; pre-signed so it should load + on F_ES
rows = []
for k, m in enumerate(cand):
    pz = PINK[m].get('raw_z') if isinstance(PINK[m].get('raw_z'), dict) else {t: PINK[m][t] for t in TR}
    bd = BD[m]
    r = {'member': m, 'interaction_ES': float(interES[k])}
    for t in TR: r['pink_' + t] = pz[t]
    for t, kk in BEH.items(): r['behav_' + t] = bd[kk]
    rows.append(r)
df = pd.DataFrame(rows).set_index('member')
for c in df.columns: df[c] = (df[c] - df[c].mean()) / df[c].std(ddof=1)
# viability diagnostic BEFORE the latent read
viab = float(np.corrcoef(df['interaction_ES'], df['pink_ES'])[0, 1])
print('CT-CM N=%d | ES indicators: pink_ES + interaction_ES (tone-free conversational-dynamics)' % len(df))
print('VIABILITY: corr(interaction_ES, pink_ES) = %+.3f' % viab)

DESC = """
F_O =~ pink_O + behav_O
F_C =~ pink_C + behav_C
F_E =~ pink_E + behav_E
F_A =~ pink_A + behav_A
F_ES =~ pink_ES + interaction_ES
M =~ pink_O + pink_C + pink_E + pink_A + pink_ES
M ~~ 0*F_O
M ~~ 0*F_C
M ~~ 0*F_E
M ~~ 0*F_A
M ~~ 0*F_ES
"""
model = semopy.Model(DESC)
try:
    model.fit(df)
except Exception as e:
    print('fit error:', str(e)[:140])
ins = model.inspect()
heywood = any(r['op'] == '~~' and r['lval'] == r['rval'] and float(r['Estimate']) < -1e-6 for _, r in ins.iterrows())
neg_load = [r['lval'] for _, r in ins.iterrows() if r['op'] == '~' and str(r['rval']).startswith('F_') and float(r['Estimate']) < 0]
try:
    sc = model.predict_factors(df); sc.index = df.index
    lat = {t: sc['F_' + t].values for t in TR}
    def pear(a, b):
        a, b = np.asarray(a, float), np.asarray(b, float)
        return float(np.corrcoef(a, b)[0, 1]) if a.std() and b.std() else 0.0
    aes = abs(pear(lat['A'], lat['ES']))
    offs = [abs(pear(lat[a], lat[b])) for i, a in enumerate(TR) for b in TR[i + 1:]]; disc = sum(offs) / len(offs)
    vc = [(lat['C'][i], PAN[m]['votes_cast']) for i, m in enumerate(df.index) if PAN.get(m, {}).get('votes_cast') is not None]
    def spearman(p):
        def rank(v):
            o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
            while i < len(o):
                j = i
                while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]: j += 1
                for kk in range(i, j + 1): r[o[kk]] = (i + j) / 2.0
                i = j + 1
            return r
        return pear(rank([x[0] for x in p]), rank([x[1] for x in p]))
    rC = spearman(vc); conv = True
except Exception as e:
    print('predict/eval error:', str(e)[:140]); aes = disc = rC = 9; conv = False
print('\n===== CT-CM latent (neuroticism-anchored) results, n=%d =====' % len(df))
print('  latent A-ES |r| = %.3f  (Pink raw A-ES = .65; RKH population floor ~.44)' % aes)
print('  latent discriminant mean off-diag = %.3f | latent C vs votes = %+.3f' % (disc, rC))
print('  Heywood: %s | negative on-trait loadings: %s | converged: %s' % (heywood, neg_load or 'none', conv))
b1 = aes <= 0.50; b2 = rC >= 0.15; b3 = (not heywood) and not neg_load and conv
print('  BAR B1 latent A-ES <= 0.50: %s (%.3f)' % ('CLEAR' if b1 else 'MISS', aes))
print('  BAR B2 latent C vs votes >= +0.15: %s (%+.3f)' % ('CLEAR' if b2 else 'MISS', rC))
print('  BAR B3 no Heywood / loadings+ / converged: %s' % ('CLEAR' if b3 else 'MISS'))
print('\nVERDICT: %s' % ('PASS — halo separated by the tone-free neuroticism anchor' if (b1 and b2 and b3) else 'MISS — per stopping rule, derived-signal route EXHAUSTED'))
