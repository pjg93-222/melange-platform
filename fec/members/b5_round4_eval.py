# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""BEMB + CTCM2 evaluator (big5_round4_prereg.md @d59bde5), reads b5_emb_cache.npz.
BEMB: split-sample (SELECT=even md5, TEST=odd) — pick winner base on SELECT, ONE look on TEST.
CTCM2: CT-CM latent with the best-available Blue as the 3rd indicator (semopy).
Modes: --select (print selection-half search), --test (the one look + promote decision), --ctcm2,
       --promote (write big5_blue_emb.json from the winner if BEMB passed)."""
import json, hashlib, collections, sys
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
B = MB_ROOT
meta = json.load(open(B + r'\data\members\b5_emb_meta.json'))
BASES = meta['bases']; MEMBERS = meta['members']
PAIRS = [tuple(p.split('|')) for p in meta['pairs']]
npz = np.load(B + r'\data\members\b5_emb_cache.npz')
def vec(base, m, g):
    k = '%s|%s|%s' % (base, m, g)
    return npz[k] if k in npz.files else None
GEN = ('CREC', 'INTERVIEW', 'social')
TOK = {tuple(k.split('|')): v for k, v in meta['toks'].items()}
BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
PAN = json.load(open(B + r'\behavior_panel.json', encoding='utf-8'))['members']
LEP = json.load(open(B + r'\data\members\lep_criterion.json', encoding='utf-8'))['per_member']
TARGET = {'A': 'cooperation_z', 'C': 'diligence_z', 'E': 'visibility_z', 'O': 'breadth_z'}
def party():
    import moneyball_core as MC; return MC.party()

def within_genre_z(base):
    """per genre: stack member vectors, z each dim across members present in that genre."""
    Z = {}
    for g in GEN:
        ms = [m for m in MEMBERS if vec(base, m, g) is not None]
        if len(ms) < 20: continue
        X = np.array([vec(base, m, g) for m in ms])
        mu, sd = X.mean(0), X.std(0, ddof=1); sd[sd == 0] = 1
        Z[g] = (ms, (X - mu) / sd)
    return Z

def member_matrix(base, Z, subset=None):
    """token-weighted avg of within-genre-z vectors -> member feature matrix."""
    idx = {g: {m: i for i, m in enumerate(Z[g][0])} for g in Z}
    rows, keep = [], []
    for m in MEMBERS:
        if subset is not None and m not in subset: continue
        num = None; den = 0.0
        for g in Z:
            if m in idx[g]:
                w = TOK.get((m, g), 1); v = Z[g][1][idx[g][m]]
                num = v * w if num is None else num + v * w; den += w
        if den > 0: rows.append(num / den); keep.append(m)
    return np.array(rows), keep

def heldout_r(X, y):
    rs = []
    for tr, te in KFold(5, shuffle=True, random_state=7).split(X):
        g = GridSearchCV(Ridge(), {'alpha': [1, 3, 10, 30, 100, 300, 1000]}, cv=3, scoring='neg_mean_squared_error')
        g.fit(X[tr], y[tr]); p = g.predict(X[te])
        rs.append(0.0 if p.std() < 1e-9 else float(np.corrcoef(p, y[te])[0, 1]))
    return float(np.mean(rs))
def fit_models(base, Z, subset):
    Xm, keep = member_matrix(base, Z, subset)
    midx = {m: i for i, m in enumerate(keep)}
    ho, models = {}, {}
    for t, tgt in TARGET.items():
        ms = [m for m in keep if m in BD and BD[m].get(tgt) is not None]
        Xa = Xm[[midx[m] for m in ms]]; ya = np.array([BD[m][tgt] for m in ms])
        ho[t] = heldout_r(Xa, ya)
        g = GridSearchCV(Ridge(), {'alpha': [1, 3, 10, 30, 100, 300, 1000]}, cv=5, scoring='neg_mean_squared_error')
        g.fit(Xa, ya); models[t] = g.best_estimator_
    return ho, models, keep

def family_mtmm(base, Z, models, subset):
    def gsc(t, g, m):
        idx = {mm: i for i, mm in enumerate(Z[g][0])}
        if m not in idx: return None
        return float(models[t].predict(Z[g][1][idx[m]].reshape(1, -1))[0])
    trs = ['A', 'C', 'E', 'O']; gg = [g for g in Z]
    same, cross = [], []
    for i in range(len(gg)):
        for j in range(i + 1, len(gg)):
            common = [m for m in (subset or MEMBERS) if m in Z[gg[i]][0] and m in Z[gg[j]][0]]
            for t in trs:
                a = np.array([gsc(t, gg[i], m) for m in common]); b = np.array([gsc(t, gg[j], m) for m in common])
                if a.std() and b.std(): same.append(float(np.corrcoef(a, b)[0, 1]))
    for g in gg:
        common = [m for m in (subset or MEMBERS) if m in Z[g][0]]
        for i, t1 in enumerate(trs):
            for t2 in trs[i + 1:]:
                a = np.array([gsc(t1, g, m) for m in common]); b = np.array([gsc(t2, g, m) for m in common])
                if a.std() and b.std(): cross.append(abs(float(np.corrcoef(a, b)[0, 1])))
    return float(np.mean(same)), float(np.mean(cross))

SEL = {m for m in MEMBERS if int(hashlib.md5(m.encode()).hexdigest(), 16) % 2 == 0}
TEST = {m for m in MEMBERS if m not in SEL}
_ZC = {}
def Zc(base):
    if base not in _ZC: _ZC[base] = within_genre_z(base)
    return _ZC[base]

def selection_search():
    print('SELECTION-half base search (%d members):' % len(SEL))
    results = []
    for base in BASES:
        Z = Zc(base)
        ho, models, _ = fit_models(base, Z, SEL)
        s, c = family_mtmm(base, Z, models, SEL)
        aco = (ho['A'] + ho['C'] + ho['O']) / 3
        results.append((base, aco, s, c, ho))
        print('  %-10s ACO-mean %+.3f | MTMM same %.3f vs cross %.3f (%s) | A%+.2f C%+.2f E%+.2f O%+.2f'
              % (base, aco, s, c, 'PASS' if s > c else 'fail', ho['A'], ho['C'], ho['E'], ho['O']))
    passing = [r for r in results if r[2] > r[3]]
    pool = passing if passing else results
    winner = max(pool, key=lambda r: (r[1], -BASES.index(r[0])))
    print('WINNER on selection: %s (ACO %+.3f, MTMM %s)' % (winner[0], winner[1], 'pass' if winner[2] > winner[3] else 'FAIL-expected'))
    return winner[0]

def test_look(winner):
    Z = Zc(winner)
    ho, models, _ = fit_models(winner, Z, TEST)
    s, c = family_mtmm(winner, Z, models, TEST)
    # E volume-control check: compare winner E vs st_volctrl E on TEST (if st bases exist)
    ev_raw = ho['E']
    ev_ctrl = None
    if 'st_volctrl' in BASES:
        Zv = Zc('st_volctrl'); hov, _, _ = fit_models('st_volctrl', Zv, TEST); ev_ctrl = hov['E']
    print('\n===== BEMB TEST look (winner=%s, %d TEST members) =====' % (winner, len(TEST)))
    print('  held-out r: A %+.3f  C %+.3f  E %+.3f  O %+.3f' % (ho['A'], ho['C'], ho['E'], ho['O']))
    print('  family MTMM: same-trait cross-genre %.3f  vs  cross-trait within-genre %.3f' % (s, c))
    if ev_ctrl is not None: print('  E volume-control: raw %+.3f vs volctrl %+.3f (delta %+.3f)' % (ev_raw, ev_ctrl, ev_raw - ev_ctrl))
    bars = [('MTMM same > 1.15x cross', s > 1.15 * c, '%.3f vs 1.15*%.3f=%.3f' % (s, c, 1.15 * c)),
            ('A >= +0.14', ho['A'] >= 0.14, '%+.3f' % ho['A']),
            ('C >= +0.20', ho['C'] >= 0.20, '%+.3f' % ho['C']),
            ('O >= +0.18', ho['O'] >= 0.18, '%+.3f' % ho['O']),
            ('E >= +0.20', ho['E'] >= 0.20, '%+.3f' % ho['E'])]
    for nm, hit, det in bars: print('  BAR %-26s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', det))
    core = all(b[1] for b in bars[:4])
    print('BEMB VERDICT: %s' % ('PASS — promote embedding Blue (E %s)' % ('robust' if (ev_ctrl is None or abs(ev_raw - ev_ctrl) <= 0.05) else 'carried from v2: volume-bound')
                                if core else 'MISS — lexical v2 stands'))
    return core, {
        'winner': winner,
        'test_n': len(TEST),
        'heldout_r': {t: round(float(ho[t]), 3) for t in TARGET},
        'mtmm_same_trait_cross_genre': round(float(s), 3),
        'mtmm_cross_trait_within_genre': round(float(c), 3),
        'mtmm_required_margin': 1.15,
        'e_volume_control_r': None if ev_ctrl is None else round(float(ev_ctrl), 3),
        'e_raw_r': round(float(ev_raw), 3),
        'bars': {name: bool(hit) for name, hit, _detail in bars},
        'verdict': 'PASS' if core else 'MISS',
    }

if __name__ == '__main__':
    if '--select' in sys.argv:
        selection_search()
    elif '--test' in sys.argv:
        w = selection_search(); test_look(w)
    elif '--promote' in sys.argv:
        # Complete Blue artifact: A/C/O from the embedding (which cleared BEMB + broke the MTMM wall),
        # E and ES CARRIED from lexical v2 (prereg: embedding-E is volume-bound; ES has no behavioral
        # criterion). Same {'traits':{m:{O,C,E,A,ES,N}}} schema as big5_v2.json so make_member_desk
        # can switch source with no shape change.
        w = selection_search()
        passed, validation = test_look(w)
        if not passed:
            raise SystemExit('BEMB promotion refused: frozen TEST gate did not pass')
        Z = Zc(w); ho, models, _ = fit_models(w, Z, set(MEMBERS))   # full-roster refit for production scores
        Xm, keep = member_matrix(w, Z, set(MEMBERS)); midx = {m: i for i, m in enumerate(keep)}
        emb = {}
        for t in ('A', 'C', 'O'):   # E excluded — volume-bound; carried from v2 below
            pred = models[t].predict(Xm); mu, sd = pred.mean(), pred.std() or 1
            for i, m in enumerate(keep): emb.setdefault(m, {})[t] = round(float((pred[i] - mu) / sd), 3)
        V2 = json.load(open(B + r'\data\members\big5_v2.json', encoding='utf-8'))['traits']
        # The N slot was independently promoted after Round 4. Keep that decision: Blue's fifth
        # axis is the affective-content N measure, not the leaky legacy lexical ES composite.
        # A coherent five-axis row is emitted only where affective-N exists; no v1/v2 N fallback
        # is mixed into the promoted instrument.
        AN = json.load(open(B + r'\data\money\affective_n.json', encoding='utf-8'))['members']
        out = {}
        for m in emb:
            if m not in AN:
                continue
            v2 = V2.get(m, {})
            n = float(AN[m])
            out[m] = {'A': emb[m]['A'], 'C': emb[m]['C'], 'O': emb[m]['O'],
                      'E': v2.get('E', 0.0), 'ES': round(-n, 3), 'N': round(n, 3),
                      'tokens': v2.get('tokens', 0), 'nposts': v2.get('nposts', 0),
                      'lowconf': bool(v2.get('lowconf', False)), 'n_source': 'affective_n'}
        from moneyball_core import dump_atomic
        dump_atomic({'traits': out,
                     'method': 'Blue source-primary = within-genre open-vocabulary embedding (base=%s) for A/C/O '
                               '[frozen TEST MTMM/criterion gate passed]; E carried from lexical v2 because embedding-E '
                               'was volume-bound; N/ES from promoted affective_n (ES=-N)' % w,
                     'heldout_full_refit_cv': {t: round(ho[t], 3) for t in TARGET},
                     'validation': validation,
                     'base': w, 'aco_from': 'within-genre embedding', 'e_from': 'v2',
                     'n_es_from': 'affective_n', 'coverage_n': len(out),
                     'source_genres': list(GEN),
                     'preregistration': 'big5_round4_prereg.md'},
                    B + r'\data\members\big5_blue_emb.json')
        print('wrote big5_blue_emb.json (A/C/O=embedding %s, E=v2, N/ES=affective-N, %d members)' % (w, len(out)))
    else:
        print('modes: --select | --test | --promote')
