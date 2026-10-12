# -*- coding: utf-8 -*-
"""PRE-REGISTERED antagonism promotion gate (locked 2026-07-11, BEFORE final collection completes --
anti-basis-shopping). Run on the FULL 2019-2026 corpus after collection finishes. Decides whether the
substance-shaped, Haiku-refined, DW-NOMINATE/seniority/party/minority-controlled dispositional
`antagonism_z` earns promotion from isolated experiment to a ratified A-antagonism behavioral FACET
(the way reactivity_z was ratified as the N-slot). There is NO gold-standard A criterion (text-A reads
E-halo, unvalidated; cooperation_v2 is the bonding pole, a different facet) -- so the gate is construct
integrity, NOT convergence with a validated criterion.

PROMOTION requires ALL FIVE (thresholds fixed in advance):
  G1 RELIABILITY   split-half Spearman-Brown SB >= 0.55  (>=4-hearing members)
  G2 CONFOUND-CLEAN combined R^2 of [party, dim1, extremity, seniority, minority-frac] on antagonism < 0.15
  G3 DISCRIMINANT   max |r| with any existing disposition {dominance,visibility,reactivity,cooperation,
                    breadth,diligence} < 0.50  (not redundant with something already in the engine)
  G4 CONSTRUCT      r(antagonism, cooperation_v2) < 0  AND r(antagonism, text-A) < 0  (both negative =
                    antagonism is the low-agreeableness pole; DIRECTION gates, magnitude is supportive)
  G5 HAIKU-CORE     the Haiku dispositional-hostility component (aggressive_interrogation + personal_attack,
                    non-partisan) is present at scale (>= 150 labeled dispositional events) and the
                    Haiku-built antagonism correlates with the lexicon-built one r >= 0.5 (same construct)

If ALL pass -> ratify as a DISPLAY + secondary A-facet (antagonism pole); do NOT replace cooperation_v2 as
the primary A basis. If ANY fail -> report honestly, keep as documented experiment, do not force it."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import members.hearing_antagonism_build as HB

from paths import ROOT
B = ROOT
GATE = {'G1_SB': 0.55, 'G2_R2': 0.15, 'G3_maxr': 0.50, 'G5_disp_n': 150, 'G5_corr': 0.50}

def r2_multi(y, Xcols):
    y = np.asarray(y, float); m = ~np.isnan(y)
    X = np.column_stack([np.ones(m.sum())] + [np.asarray(c, float)[m] for c in Xcols])
    yy = y[m]; beta, *_ = np.linalg.lstsq(X, yy, rcond=None); pred = X @ beta
    ss = ((yy - yy.mean()) ** 2).sum()
    return 1 - ((yy - pred) ** 2).sum() / ss if ss > 0 else 0.0

def main():
    DW, PTY, agg, byhear, minority, opp, audit, nh = HB.build()
    SEN = HB.load_seniority()
    BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
    COV = json.load(open(B + r'\coop_v2_candidate.json'))['members']
    A5 = json.load(open(B + r'\data\members\big5_v3_hybrid.json')).get('traits', {})
    MIN = 20
    mem = [m for m in agg if agg[m]['turns'] >= MIN and m in DW]
    tr = np.array([agg[m]['turns'] for m in mem], float)
    def rate(k): return np.array([agg[m].get(k, 0) for m in mem]) / tr
    # antagonism_z: prefer Haiku dispositional core if labels present, else lexicon personal
    tot_lab = sum(agg[m].get('h_labeled', 0) for m in mem)
    disp = rate('h_disp') if tot_lab > GATE['G5_disp_n'] else rate('fr_personal')
    antag_raw = HB.z(rate('mm_intr')) + HB.z(rate('mm_xparty')) + HB.z(disp)
    ext = np.array([DW[m]['extremity'] for m in mem]); d1 = np.array([DW[m]['dim1'] for m in mem])
    pty = np.array([1.0 if PTY.get(m) == 'R' else 0.0 for m in mem])
    minf = np.array([minority[m][0] / minority[m][1] if minority[m][1] else 0.5 for m in mem])
    sen = np.array([SEN.get(m, np.nan) for m in mem]); sen = np.where(np.isnan(sen), np.nanmean(sen), sen)
    antag = HB.resid(antag_raw, [sen, ext, d1, pty, minf])
    def bdv(k): return np.array([BD.get(m, {}).get(k, np.nan) if isinstance(BD.get(m), dict) else np.nan for m in mem])

    print('=== ANTAGONISM PROMOTION GATE | %d hearings | %d members | Haiku labels=%d ===' % (nh, len(mem), tot_lab))
    res = {}
    # G1 reliability
    memR = [m for m in mem if len(byhear[m]) >= 4]
    def half(w):
        out = []
        for m in memR:
            h = byhear[m][w::2]; t = max(sum(x[0] for x in h), 1); out.append((sum(x[1] for x in h)) / t)
        return HB.z(out)
    rr = HB.corr(half(0), half(1)); sb = 2 * rr / (1 + rr) if rr > -1 else float('nan')
    res['G1'] = sb >= GATE['G1_SB']; print('  G1 reliability SB = %+.3f  (>= %.2f) -> %s' % (sb, GATE['G1_SB'], 'PASS' if res['G1'] else 'FAIL'))
    # G2 confound R^2
    r2 = r2_multi(antag_raw, [pty, d1, ext, sen, minf])
    res['G2'] = r2 < GATE['G2_R2']; print('  G2 confound R^2 = %.3f  (< %.2f) -> %s' % (r2, GATE['G2_R2'], 'PASS' if res['G2'] else 'FAIL'))
    # G3 discriminant
    disc = {k: HB.corr(antag, bdv(k)) for k in ['dominance_z', 'visibility_z', 'reactivity_z', 'cooperation_z', 'breadth_z', 'diligence_z']}
    mx = max(abs(v) for v in disc.values()); res['G3'] = mx < GATE['G3_maxr']
    print('  G3 discriminant max|r| = %.3f (%s)  (< %.2f) -> %s' % (mx, max(disc, key=lambda k: abs(disc[k])), GATE['G3_maxr'], 'PASS' if res['G3'] else 'FAIL'))
    # G4 construct direction
    rc = HB.corr(antag, [COV.get(m, {}).get('c1_bip_z', np.nan) for m in mem])
    ra = HB.corr(antag, [A5.get(m, {}).get('A', np.nan) for m in mem])
    res['G4'] = (rc < 0) and (ra < 0); print('  G4 construct: r(coop_v2)=%+.3f r(text-A)=%+.3f (both<0) -> %s' % (rc, ra, 'PASS' if res['G4'] else 'FAIL'))
    # G5 Haiku core
    hd_n = sum(agg[m].get('h_disp', 0) for m in mem)
    antag_lex = HB.resid(HB.z(rate('mm_intr')) + HB.z(rate('mm_xparty')) + HB.z(rate('fr_personal')), [sen, ext, d1, pty, minf])
    rhl = HB.corr(antag, antag_lex)
    res['G5'] = (hd_n >= GATE['G5_disp_n']) and (rhl >= GATE['G5_corr'])
    print('  G5 Haiku-core: dispositional events=%d (>=%d), r(haiku,lexicon)=%+.3f (>=%.2f) -> %s' % (hd_n, GATE['G5_disp_n'], rhl, GATE['G5_corr'], 'PASS' if res['G5'] else 'FAIL'))

    npass = sum(res.values())
    print('\n  === VERDICT: %d/5 gates passed -> %s ===' % (npass, 'PROMOTE (ratify as A-antagonism facet)' if npass == 5 else 'DO NOT PROMOTE (keep as experiment; report honestly)'))
    ao = sorted(range(len(mem)), key=lambda i: -antag[i])
    print('  top antagonists (final): %s' % ', '.join('%s(%s)' % (mem[i], PTY.get(mem[i])) for i in ao[:10]))
    return res

if __name__ == '__main__':
    main()
