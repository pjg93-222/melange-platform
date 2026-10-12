# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""Audio-prosody pilot — THE ONE LOOK (big5_audio_prereg.md @3c94fc7). Reads b5_audio_cache.json +
behavioral_dispositions + big5_v2 (text). Enforces the two LOCKED bars:
  BAR 1 (incremental validity, E): audio-alone held-out r for E >= +0.15 AND text+audio fused beats
        text-alone by >= +0.03 (visibility_z criterion).
  BAR 2 (halo separation): within-audio A-ES |r| <= 0.45 (audio-A criterion-fit to cooperation_z;
        ES_acoustic = -z(F0stddev)-z(jitter)-z(shimmer)-z(loudness_stddev), theory-driven/unsupervised).
PASS = both. A pass is adversarially re-derived before it is recorded (P6 doctrine). --synth tests
the math offline on a random cache (proves the code + bar logic run; NOT a result).

!! NOTE (2026-07-09): the BAR-2 ES_acoustic composite above is MIS-SIGNED (folk 'steady voice = calm';
!! clean-data + literature show flat/monotone tracks INSTABILITY -- see b5_audio_ctcm.py header note).
!! The recorded MISS stands as the historical one-look; do not reuse this composite's signs. The
!! authoritative ES cross-modal result = fec/members/b5_es_crossmodal.py (held-out r=+0.208, perm-p=.0002)."""
import json
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV, cross_val_predict
sys = _s; sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
TARGET = {'A': 'cooperation_z', 'C': 'diligence_z', 'E': 'visibility_z', 'O': 'breadth_z'}
ES_FEATS = ['F0semitoneFrom27.5Hz_sma3nz_stddevNorm', 'jitterLocal_sma3nz_amean',
            'shimmerLocaldB_sma3nz_amean', 'loudness_sma3_stddevNorm']
ALPHAS = [1, 3, 10, 30, 100, 300, 1000]
SEEDS = [7, 17, 29, 43, 61]   # repeated 5-fold CV: at n~40/p=88 a single split is high-variance
                              # (audit smoke: random features cleared +0.15 on one seed) -> average to stabilize.

def heldout_r(X, y, seeds=SEEDS):
    rs = []
    for s in seeds:
        for tr, te in KFold(5, shuffle=True, random_state=s).split(X):
            g = GridSearchCV(Ridge(), {'alpha': ALPHAS}, cv=3, scoring='neg_mean_squared_error')
            g.fit(X[tr], y[tr]); p = g.predict(X[te])
            rs.append(0.0 if p.std() < 1e-9 else float(np.corrcoef(p, y[te])[0, 1]))
    return float(np.mean(rs))

def perm_null(X, y, K=40):
    """95th-pct held-out r under label shuffling, using the SAME repeated-CV estimator as the observed
    statistic = the fair noise floor at this n/p. Printed next to the primary bar as a diagnostic;
    does NOT change the locked bar (the P6 re-derivation is the gate)."""
    rng = np.random.RandomState(0)
    null = [heldout_r(X, rng.permutation(y)) for _ in range(K)]
    return float(np.percentile(null, 95))

def cv_pred(X, y):
    g = GridSearchCV(Ridge(), {'alpha': ALPHAS}, cv=3, scoring='neg_mean_squared_error')
    return cross_val_predict(g, X, y, cv=KFold(5, shuffle=True, random_state=7))

def run(cache):
    names = cache['feature_names']; Z = cache['z']
    members = [m for m in cache['members'] if m in Z]
    BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
    try: V2 = json.load(open(B + r'\data\members\big5_v2.json', encoding='utf-8'))['traits']
    except Exception: V2 = {}
    idx = {n: i for i, n in enumerate(names)}
    A = np.array([Z[m] for m in members])                       # audio feature matrix (members x 88)
    print('=== AUDIO PILOT — ONE LOOK (n=%d members) ===' % len(members))

    # ---- BAR 1: incremental validity on E ----
    def yvec(tr): return np.array([(BD.get(m, {}) or {}).get(TARGET[tr]) for m in members], dtype=float)
    def textcol(tr): return np.array([(V2[m].get(tr) if m in V2 else None) for m in members], dtype=float)
    def zc(v): s = v.std(ddof=1); return (v - v.mean()) / (s if s > 1e-9 else 1.0)
    res = {}                                              # audio-alone held-out r per trait (pilot slice)
    for tr in ('E', 'A', 'C', 'O'):
        y = yvec(tr); ok = ~np.isnan(y)
        res[tr] = heldout_r(A[ok], y[ok]) if ok.sum() >= 20 else None
    audio_E = res['E']
    yE = yvec('E'); okE = ~np.isnan(yE); tE = textcol('E')
    nullE = perm_null(A[okE], yE[okE]) if audio_E is not None else None   # noise floor at this n/p
    okF = okE & ~np.isnan(tE)                             # audio + text + E-criterion: the fair incremental set
    if okF.sum() >= 20:
        tz = zc(tE[okF])[:, None]                         # z the text col -> commensurate w/ z-audio (fair ridge penalty)
        text_only = heldout_r(tz, yE[okF]); fused = heldout_r(np.hstack([tz, A[okF]]), yE[okF])
    else:
        text_only = fused = float('nan')
    above = (audio_E is not None and nullE is not None and audio_E > nullE)   # one-sided permutation p<.05
    b1a = (audio_E is not None and audio_E >= 0.15 and above)                 # bar AND significant vs the n-aware noise floor
    b1b = (fused - text_only) >= 0.03
    print('BAR 1 incremental validity (E vs visibility_z):')
    print('  audio-alone E held-out r = %+.3f  (n=%d, needs >= +0.15 AND > perm-null 95%%=%+.3f) -> %s (%s)'
          % (audio_E or 0, int(okE.sum()), nullE or 0, 'CLEAR' if b1a else 'MISS',
             'above noise' if above else 'WITHIN NOISE'))
    print('  text-alone E %+.3f | text+audio fused %+.3f | delta %+.3f  (n=%d, bar >= +0.03) -> %s'
          % (text_only, fused, fused - text_only, int(okF.sum()), 'CLEAR' if b1b else 'MISS'))
    print('  secondary audio-alone (no bar): A%+.3f C%+.3f O%+.3f'
          % (res.get('A') or 0, res.get('C') or 0, res.get('O') or 0))
    print('  (n~40 vs 88 eGeMAPS features: ridge alpha grid to 1000 regularizes; text col z-scored for fair fusion)')
    bar1 = b1a and b1b

    # ---- BAR 2: within-audio A-ES halo ----
    miss = [f for f in ES_FEATS if f not in idx]
    if miss:
        print('BAR 2: ES-index features missing from cache: %s -> cannot evaluate' % miss); bar2 = False; aes = 9
    else:
        es_cols = np.array([[Z[m][idx[f]] for f in ES_FEATS] for m in members])
        ES_acoustic = -es_cols.mean(1)                          # low variability/perturbation = steady = ES+
        yA = yvec('A'); okA = ~np.isnan(yA)
        audioA = cv_pred(A[okA], yA[okA])                       # criterion-fit audio-A (cross-validated)
        esA = ES_acoustic[okA]
        aes = abs(float(np.corrcoef(audioA, esA)[0, 1])) if audioA.std() and esA.std() else 9
        # separation is interpretable ONLY if audio-A is a VALID A measure — else low A-ES is vacuous
        # (random audio has no A signal, so it 'separates' trivially). Gate on audio-A significance.
        audio_A = res.get('A'); nullA = perm_null(A[okA], yA[okA]) if audio_A is not None else None
        a_valid = (audio_A is not None and nullA is not None and audio_A > nullA)
        bar2 = (aes <= 0.45) and a_valid
        print('BAR 2 halo separation: within-audio A-ES |r| = %.3f (bar <= 0.45; text halo .65) | '
              'audio-A valid = %s (r %+.3f vs null %+.3f) -> %s'
              % (aes, a_valid, audio_A or 0, nullA or 0, 'CLEAR' if bar2 else 'MISS'))

    verdict = bar1 and bar2
    print('\nAUDIO PILOT VERDICT: %s' % ('PASS — adversarially re-derive before recording (P6 doctrine)'
                                         if verdict else 'MISS — text terminus stands; documented, no number moves'))
    return {'n': len(members), 'audio_E': audio_E, 'perm_null_E': nullE, 'text_E': text_only, 'fused_E': fused,
            'audio_ACO': {t: res.get(t) for t in ('A', 'C', 'O')}, 'a_es': aes, 'bar1': bar1, 'bar2': bar2, 'pass': verdict}

if __name__ == '__main__':
    if '--synth' in sys.argv:
        rng = np.random.RandomState(0); n = 60
        Aarr = rng.randn(n, 88); y = rng.randn(n)
        print('--synth: exercising heldout_r / cv_pred / bar helpers on random features (expect ~0)')
        print('  heldout_r(random) = %+.3f' % heldout_r(Aarr, y))
        es = -Aarr[:, :4].mean(1); ap = cv_pred(Aarr, y)
        print('  A-ES(random) = %.3f' % abs(np.corrcoef(ap, es)[0, 1]))
        print('  math + bar helpers execute cleanly.')
    else:
        cache = json.load(open(B + r'\data\members\b5_audio_cache.json', encoding='utf-8'))
        run(cache)
