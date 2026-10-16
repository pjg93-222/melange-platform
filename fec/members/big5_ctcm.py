# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""CT-CM latent capstone — big5_round3_prereg.md Addendum 2 (@3894fe6), ONE look.
Correlated-traits, correlated-methods structural model over the EXISTING instruments (nothing is
recalibrated; instrument independence intact): per Big-5 dimension a latent trait indicated by
pink (LLM raw_z, pre-shrinkage), blue (v2), and the behavioral disposition (A<-cooperation,
C<-diligence, E<-visibility, O<-breadth; ES has no behavioral criterion -> pink+blue only), plus
a PINK METHOD factor loading on all five pink indicators (absorbing shared judge variance -- the
halo), orthogonal to the traits.
Bars (one look): latent A-ES |r| <= 0.50 (RKH anchor .443); latent C vs votes_cast Spearman >=
+0.15; latent discriminant mean off-diag |r| <= 0.30; all on-trait loadings positive, no Heywood.
PASS -> big5_latent.json ships (desk overlay artifact) + audit guard. MISS -> documented; the
psychology campaign closes with the P6 + retest gains and the halo documented against RKH."""
import json, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd
import semopy
B = MB_ROOT

PINK = json.load(open(B + r'\data\members\big5_llm_opus.json', encoding='utf-8'))['traits']
BLUE = json.load(open(B + r'\data\members\big5_v2.json', encoding='utf-8'))['traits']
BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
PAN = json.load(open(B + r'\behavior_panel.json', encoding='utf-8'))['members']
TR = ['O', 'C', 'E', 'A', 'ES']
BEH = {'A': 'cooperation_z', 'C': 'diligence_z', 'E': 'visibility_z', 'O': 'breadth_z'}

rows = []
for m, p in PINK.items():
    if m not in BLUE: continue
    pz = p.get('raw_z') if isinstance(p.get('raw_z'), dict) else {t: p[t] for t in TR}
    r = {'member': m}
    for t in TR:
        r['pink_' + t] = pz[t]
        r['blue_' + t] = BLUE[m].get(t)
    ok = True
    for t, key in BEH.items():
        v = BD.get(m, {}).get(key)
        if v is None: ok = False
        r['behav_' + t] = v
    if ok and all(r['blue_' + t] is not None for t in TR): rows.append(r)
df = pd.DataFrame(rows).set_index('member')
for c in df.columns: df[c] = (df[c] - df[c].mean()) / df[c].std(ddof=1)
print('CT-CM complete-case members: %d | indicators: %d' % (len(df), len(df.columns)))

DESC = """
F_O =~ pink_O + blue_O + behav_O
F_C =~ pink_C + blue_C + behav_C
F_E =~ pink_E + blue_E + behav_E
F_A =~ pink_A + blue_A + behav_A
F_ES =~ pink_ES + blue_ES
M =~ pink_O + pink_C + pink_E + pink_A + pink_ES
M ~~ 0*F_O
M ~~ 0*F_C
M ~~ 0*F_E
M ~~ 0*F_A
M ~~ 0*F_ES
"""
model = semopy.Model(DESC)
res = model.fit(df)
print('fit:', res)
ins = model.inspect()
heywood = False; badload = []
for _, r in ins.iterrows():
    if r['op'] == '~~' and r['lval'] == r['rval'] and float(r['Estimate']) < 0: heywood = True
for t in TR:
    for ind in ('pink_', 'blue_') + (('behav_',) if t in BEH else ()):
        sel = ins[(ins['op'] == '~') & (ins['lval'] == ind + t) & (ins['rval'] == 'F_' + t)]
        if len(sel) and float(sel['Estimate'].iloc[0]) < 0: badload.append(ind + t)
        # (first indicator per factor is fixed to 1 by semopy convention -> positive by construction)
try:
    fs = semopy.calc_stats(model)
    cols = [c for c in ('DoF', 'chi2', 'CFI', 'TLI', 'RMSEA') if c in fs.columns]
    print('fit stats:', fs[cols].iloc[0].to_dict() if cols else fs.columns.tolist())
except Exception as _e:
    print('fit stats unavailable:', str(_e)[:80])
scores = model.predict_factors(df)
scores.index = df.index
lat = {t: scores['F_' + t] for t in TR}
def pear(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return float(np.corrcoef(a, b)[0, 1]) if a.std() and b.std() else 0.0
aes = abs(pear(lat['A'], lat['ES']))
offs = [abs(pear(lat[a], lat[b])) for i, a in enumerate(TR) for b in TR[i + 1:]]
disc = sum(offs) / len(offs)
vc = [(lat['C'][m], PAN[m]['votes_cast']) for m in df.index if PAN.get(m, {}).get('votes_cast') is not None]
def spearman(pairs):
    def rk(v):
        order = sorted(range(len(v)), key=lambda i: v[i]); out = [0.0] * len(v); i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]: j += 1
            for kk in range(i, j + 1): out[order[kk]] = (i + j) / 2 + 1
            i = j + 1
        return out
    return pear(rk([p[0] for p in pairs]), rk([p[1] for p in pairs]))
rC = spearman(vc)
print('\n===== CT-CM latent results (n=%d) =====' % len(df))
print('  latent A-ES |r| = %.3f | latent discriminant mean = %.3f' % (aes, disc))
print('  latent C vs votes_cast Spearman = %+.3f (n=%d)' % (rC, len(vc)))
print('  Heywood: %s | negative on-trait loadings: %s' % (heywood, badload or 'none'))
bars = [('latent A-ES <= 0.50', aes <= 0.50, '%.3f' % aes),
        ('latent discriminant <= 0.30', disc <= 0.30, '%.3f' % disc),
        ('latent C vs votes >= +0.15', rC >= 0.15, '%+.3f' % rC),
        ('loadings positive, no Heywood', (not heywood) and not badload, str(badload) if badload else 'ok'),
        ('optimizer converged', bool(res.success), str(res.message))]
for nm, hit, det in bars: print('  BAR %-30s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', det))
ok = all(b[1] for b in bars)
if ok:
    from moneyball_core import dump_atomic
    dump_atomic({'members': {m: {t: round(float(lat[t][m]), 3) for t in TR} for m in df.index},
                 'method': 'CT-CM latent (semopy): traits =~ pink(raw_z)+blue(v2)+behavioral; '
                           'pink METHOD factor orthogonal to traits absorbs judge halo. '
                           'Prereg Addendum-2 @3894fe6, one look, all bars cleared.',
                 'n': len(df), 'latent_a_es': round(aes, 3), 'latent_disc': round(disc, 3),
                 'latent_c_votes': round(rC, 3)}, B + r'\big5_latent.json')
    print('CTCM VERDICT: PASS — big5_latent.json shipped (desk overlay artifact)')
else:
    print('CTCM VERDICT: MISS — documented; campaign closes with P6 + retest gains, halo documented vs RKH')
