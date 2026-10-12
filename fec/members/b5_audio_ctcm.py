# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Round-2 TEST B (big5_audio_round2_prereg.md @7f41388): CT-CM halo attack with the validated
INDEPENDENT acoustic-ES indicator. ES previously had only pink_ES (1 indicator, unidentified / halo
inseparable); the pure acoustic-ES composite (steady/dynamic-voice markers, r=-.249 w/ Pink-ES, p=.018,
NEVER fit to Pink) gives ES its independent 2nd indicator. Behavioral cooperation anchors A. The Pink
METHOD factor absorbs the judge halo. Instruments' own scores UNTOUCHED. ONE LOOK.
Bars: latent A-ES |r| <= 0.50 (vs Pink .65); latent C vs votes_cast Spearman >= +0.15; no Heywood.

!! DEPRECATED / MIS-SIGNED (2026-07-09, es-voice-recheck workflow + clean-audio re-test): the SIGN
!! dict below encodes a folk 'steady/clean-toned voice = calm' model. On the 395 identity-verified
!! clean voiceprints its significant features track ES the OPPOSITE way (F0stddevNorm +.14, jitter
!! +.15, shimmer +.15, HNR -.18 vs big5_v2-ES, all p<.01) -- a monotone/flat/clean voice is the
!! depression/withdrawal (instability) signature, not stability. This composite's -0.165 was real
!! signal read upside-down, and the latent MISS above inherited the inverted anchor. The result is
!! kept UNCHANGED as the historical one-look record; do NOT re-run or trust this composite's signs.
!! Authoritative ES cross-modal test = fec/members/b5_es_crossmodal.py (sign-choice-free ridge, held-out
!! r=+0.208 perm-p=.0002 ES-specific; big5_es_crossmodal_prereg.md RESULT)."""
import json
sys = _s; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd, semopy
B = MB_ROOT
AU = json.load(open(B + r'\data\members\b5_audio_cache.json', encoding='utf-8')); MEM = AU['members']; FN = AU['feature_names']
V = np.array([AU['z'][m] for m in MEM])
PINK = json.load(open(B + r'\data\members\big5_llm_opus.json', encoding='utf-8'))['traits']
BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
PAN = json.load(open(B + r'\behavior_panel.json', encoding='utf-8'))['members']
# pure acoustic-ES composite (a-priori steady/dynamic-voice markers; independent of Pink)
SIGN = {'loudness_sma3_stddevNorm': -1, 'F0semitoneFrom27.5Hz_sma3nz_stddevNorm': -1,
        'jitterLocal_sma3nz_amean': -1, 'shimmerLocaldB_sma3nz_amean': -1,
        'HNRdBACF_sma3nz_amean': +1, 'spectralFlux_sma3_amean': -1}
cols = [(FN.index(f), s) for f, s in SIGN.items() if f in FN]
audioES = np.mean([s * V[:, i] for i, s in cols], axis=0)
TR = ['O', 'C', 'E', 'A', 'ES']; BEH = {'O': 'breadth_z', 'C': 'diligence_z', 'E': 'visibility_z', 'A': 'cooperation_z'}
rows = []
for k, m in enumerate(MEM):
    pz = PINK[m].get('raw_z') if isinstance(PINK[m].get('raw_z'), dict) else {t: PINK[m][t] for t in TR}
    bd = BD.get(m) or {}
    if any(bd.get(BEH[t]) is None for t in BEH): continue
    r = {'member': m, 'audio_ES': float(audioES[k])}
    for t in TR: r['pink_' + t] = pz[t]
    for t, kk in BEH.items(): r['behav_' + t] = bd[kk]
    rows.append(r)
df = pd.DataFrame(rows).set_index('member')
for c in df.columns: df[c] = (df[c] - df[c].mean()) / df[c].std(ddof=1)
print('CT-CM complete-case N=%d | ES indicators: pink_ES + audio_ES (independent, acoustic)' % len(df))

DESC = """
F_O =~ pink_O + behav_O
F_C =~ pink_C + behav_C
F_E =~ pink_E + behav_E
F_A =~ pink_A + behav_A
F_ES =~ pink_ES + audio_ES
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
                for k in range(i, j + 1): r[o[k]] = (i + j) / 2.0
                i = j + 1
            return r
        return pear(rank([x[0] for x in p]), rank([x[1] for x in p]))
    rC = spearman(vc); conv = True
except Exception as e:
    print('predict/eval error:', str(e)[:140]); aes = disc = rC = 9; conv = False
print('\n===== CT-CM latent (audio-anchored) results, n=%d =====' % len(df))
print('  latent A-ES |r| = %.3f  (Pink raw A-ES = .65)' % aes)
print('  latent discriminant mean off-diag = %.3f | latent C vs votes = %+.3f' % (disc, rC))
print('  Heywood: %s | negative on-trait loadings: %s | converged: %s' % (heywood, neg_load or 'none', conv))
b1 = aes <= 0.50; b2 = rC >= 0.15; b3 = (not heywood) and not neg_load and conv
print('  BAR B1 latent A-ES <= 0.50: %s (%.3f)' % ('CLEAR' if b1 else 'MISS', aes))
print('  BAR B2 latent C vs votes >= +0.15: %s (%+.3f)' % ('CLEAR' if b2 else 'MISS', rC))
print('  BAR B3 no Heywood / loadings+ / converged: %s' % ('CLEAR' if b3 else 'MISS'))
print('\nVERDICT: %s' % ('PASS — halo separated by the independent audio-ES anchor' if (b1 and b2 and b3) else 'MISS — documented'))
