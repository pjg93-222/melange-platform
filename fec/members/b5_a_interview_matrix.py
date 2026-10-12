# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""Locked two-stage A successor harness: fresh interview antagonism criterion, then st_mean A look.

``--criterion`` evaluates only the predeclared criterion-quality gates. ``--look`` is armed only
after that transcript and the label/input fingerprints are committed into the prereg. No artifact
is consumed by production; every result is printed for verbatim append to the prereg.
"""
import argparse, csv, hashlib, json, math, re, sqlite3
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import GridSearchCV, KFold
_s.stdout.reconfigure(encoding='utf-8', errors='replace')
import members.b5_behavioral_instrument as BI
import members.b5_a_stratum_diag as AD
import members.b5_n_matrix as NM
import moneyball_core as MC

B = MB_ROOT
PREREG = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))),
                      'big5_a_interview_prereg.md')
LABEL = B + r'\data\stance\interview_antagonism_halves.json.candidate'
RESEEDS = [101, 113, 127, 139, 151]
PINS = {
    'label': '15ffb00ba199708c17733260205e0580ce766f3b37e0c69a74965b35b4ab0575',
    'coop': 'f0f958dfd35e89f7ab08e73e4f4aab59f762e50558803da4f6dec3a455ff224c',
    'hearing': '7eb389593de20dd555fb3d23ffffa3e7fc48d6cbf2611434548ec06b5d282206',
    'bd': '57ca1e2b1731cbeed4411645c2b527a8db0136362c2d3d4654dda8faaa852e03',
    'vv': '0bf7c461e9eca440535484bf325c5b90d423daf0573e984a1b8691a242194751',
    'panel': '491761ea130f8458ae0d09d15be20619b31011ab14e56e6a05bfbdf3d703a4cb',
    'st_npz': '69b1da00ebcc7bf4ecdbb07676630f39f6bbb01591ad8e8f8a83f6f4edd17619',
    'st_meta': 'e3189a7a4fcc5d13c4815da65481e5393ff0804d50cb2169f3a14a74ea0f2ea8',
    'empath': 'fd5ff76d59f59ed67889d1dd6a2e2f9d42ca596122bf831ae1a126b5d9d7383f',
    'speeches': 268173, 'posts': 29098,
}

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()

def rr(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    return float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() >= 8 else float('nan')

def zz(x):
    x = np.asarray(x, float); return (x - x.mean()) / (x.std(ddof=1) or 1.0)

def resid(y, cols):
    X = np.column_stack([np.ones(len(y))] + [np.asarray(c, float) for c in cols])
    return y - X @ np.linalg.lstsq(X, y, rcond=None)[0]

def demographics(names, words):
    P = MC.party(); b2n = MC.bio2name(); gender = {}
    with open(B + r'\fec\legislators.csv', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            m = b2n.get(row.get('bioguide_id'))
            if m: gender[m] = row.get('gender', '')
    party = np.array([1.0 if P.get(m) == 'R' else 0.0 for m in names])
    female = np.array([1.0 if gender.get(m) == 'F' else 0.0 for m in names])
    logw = np.log1p(np.asarray(words, float))
    return party, female, logw

def load_scores():
    raw = json.load(open(LABEL, encoding='utf-8'))
    rows = []
    for name, rec in raw.get('members', {}).items():
        try:
            a, b = rec['halves']['A'], rec['halves']['B']
            rows.append((name, float(a['antagonism']), float(b['antagonism']),
                         (float(a['policy_conflict']) + float(b['policy_conflict'])) / 2,
                         (float(a['principled_dissent']) + float(b['principled_dissent'])) / 2,
                         sum(rec['words'])))
        except (KeyError, TypeError, ValueError):
            pass
    rows.sort()
    return rows

def criterion_report():
    rows = load_scores(); names = [r[0] for r in rows]
    h1 = np.array([r[1] for r in rows]); h2 = np.array([r[2] for r in rows])
    ant = (h1 + h2) / 2; policy = np.array([r[3] for r in rows]); dissent = np.array([r[4] for r in rows])
    party, female, logw = demographics(names, [r[5] for r in rows])
    rh = rr(h1, h2); sb = 2 * rh / (1 + rh) if rh > -1 else float('nan')
    conf = {'party': rr(ant, party), 'gender': rr(ant, female), 'log_words': rr(ant, logw)}
    H = json.load(open(B + r'\data\stance\hearing_antagonism.json', encoding='utf-8'))['members']
    CV = json.load(open(B + r'\coop_v2_candidate.json', encoding='utf-8'))['members']
    hm = [m for m in names if m in H]
    cm = [m for m in names if (CV.get(m) or {}).get('cooperation_v2_z') is not None]
    amap = dict(zip(names, ant))
    hear = rr([amap[m] for m in hm], [H[m]['antagonism_z'] for m in hm])
    coop = rr([amap[m] for m in cm], [CV[m]['cooperation_v2_z'] for m in cm])
    excl = max(abs(rr(ant, policy)), abs(rr(ant, dissent)))
    gates = {
        'G1': len(rows) >= 250 and sb >= .60,
        'G2': abs(conf['party']) <= .20 and abs(conf['gender']) <= .15 and abs(conf['log_words']) <= .20,
        'G3': len(hm) >= 150 and hear >= .15,
        'G4': len(cm) >= 180 and coop < 0,
        'G5': excl < .50,
    }
    print('=== INTERVIEW-ANTAGONISM CRITERION GATE ===')
    print('  G1 coverage/reliability: n=%d half-r=%+.3f SB=%.3f (n>=250, SB>=.60) -> %s' %
          (len(rows), rh, sb, 'PASS' if gates['G1'] else 'FAIL'))
    print('  G2 raw confounds: party=%+.3f gender=%+.3f log_words=%+.3f -> %s' %
          (conf['party'], conf['gender'], conf['log_words'], 'PASS' if gates['G2'] else 'FAIL'))
    print('  G3 cross-venue hearing convergence: r=%+.3f n=%d -> %s' %
          (hear, len(hm), 'PASS' if gates['G3'] else 'FAIL'))
    print('  G4 cooperation direction: r=%+.3f n=%d (must <0) -> %s' %
          (coop, len(cm), 'PASS' if gates['G4'] else 'FAIL'))
    print('  G5 excluded-content discriminant max|r|=%.3f (<.50) -> %s' %
          (excl, 'PASS' if gates['G5'] else 'FAIL'))
    ok = all(gates.values())
    print('  CRITERION VERDICT: %d/5 -> %s' % (sum(gates.values()), 'QUALIFIED' if ok else 'REJECTED; A look forbidden'))
    return ok, names, ant, party, female, logw

def oof_all(X, y, mask, seeds):
    pred = np.zeros((len(seeds), len(y)))
    for si, seed in enumerate(seeds):
        for tr, te in KFold(5, shuffle=True, random_state=seed).split(X):
            tr = np.array([i for i in tr if mask[i]], int)
            fit = GridSearchCV(Ridge(), {'alpha': BI.ALPHAS}, cv=3, scoring='neg_mean_squared_error')
            fit.fit(X[tr], y[tr]); pred[si, te] = fit.predict(X[te])
    return pred.mean(0)

def eval_bars(predA, yA, pred_core, crit_core, mask, Z, label):
    own, pp, _ = BI.exact_perm_p(predA[mask], yA)
    row = all(abs(own) >= abs(rr(predA[mask], crit_core[t][mask])) for t in ['O', 'C', 'E', 'ES']) and own > 0
    rivals = {t: abs(rr(pred_core[t][mask], yA)) for t in ['O', 'C', 'E', 'ES']}
    col = all(abs(own) > v for v in rivals.values())
    part, partp = NM.fw_partial(predA[mask], yA, Z)
    passed = own >= .15 and pp < .05 and row and col and part >= .10 and partp < .05
    print('  [%s] own=%+.3f p=%.4f row=%s col=%s max-rival=%.3f partial=%+.3f p=%.4f -> %s' %
          (label, own, pp, row, col, max(rivals.values()), part, partp, 'PASS' if passed else 'MISS'))
    return passed, own

def look():
    pre = open(PREREG, encoding='utf-8').read()
    if 'STATUS: LOOK LOCKED' not in pre or '\n## RESULT-AI' in pre:
        print('LOOK REFUSED: prereg not armed or result already consumed'); return
    paths = {'label': LABEL, 'coop': B + r'\coop_v2_candidate.json',
             'hearing': B + r'\data\stance\hearing_antagonism.json', 'bd': B + r'\behavioral_dispositions.json',
             'vv': B + r'\data\members\voteview_consistency.json', 'panel': B + r'\behavior_panel.json',
             'st_npz': B + r'\data\members\b5_emb_cache_r6_st.npz', 'st_meta': B + r'\data\members\b5_emb_meta_r6_st.json',
             'empath': B + r'\data\members\b5_empath_cache.json'}
    current = {k: sha(p) for k, p in paths.items()}
    con = sqlite3.connect(B + r'\social_media.db'); con.execute('PRAGMA query_only=1')
    current['speeches'] = con.execute('select count(*) from speeches').fetchone()[0]
    current['posts'] = con.execute('select count(*) from posts').fetchone()[0]; con.close()
    bad = [k for k in PINS if current.get(k) != PINS[k]]
    if bad:
        print('LOOK REFUSED (not consumed): fingerprint drift in %s' % ','.join(bad)); return
    ok, names, ant, party, female, logw = criterion_report()
    if not ok:
        print('LOOK REFUSED (not consumed): criterion gate no longer qualifies'); return
    BD, VV, P, MEM, alltext = BI.load_corpus(); idx = {m: i for i, m in enumerate(MEM)}
    keep = [m for m in names if m in idx]; mask = np.array([m in set(keep) for m in MEM])
    amap = dict(zip(names, ant)); pmap = dict(zip(names, party)); fmap = dict(zip(names, female)); wmap = dict(zip(names, logw))
    # Agreeableness pole = low antagonism after controlling, not crediting, party/gender/volume.
    antk = np.array([amap[m] for m in keep]); pk = np.array([pmap[m] for m in keep])
    fk = np.array([fmap[m] for m in keep]); wk = np.array([wmap[m] for m in keep])
    yA = -zz(resid(antk, [pk, fk, wk])); Z = np.column_stack([pk, fk, wk])
    CV = json.load(open(B + r'\coop_v2_candidate.json', encoding='utf-8'))['members']
    cy = np.array([(CV.get(m) or {}).get('cooperation_v2_z', np.nan) for m in MEM], float)
    cmask = np.isfinite(cy)
    Xst, km = AD.member_matrix('st_mean', MEM)
    if km != MEM: raise RuntimeError('st_mean member order/coverage drift')
    predA = oof_all(Xst, cy, cmask, BI.SEEDS)
    Xemp = BI.featurize(MEM, alltext); crit = BI.criteria(BD, VV, P, MEM)
    core = {t: BI.oof(Xemp, crit[t]) for t in ['O', 'C', 'E', 'ES']}
    print('input fingerprints: MATCH | MEM=%d | fresh-A n=%d' % (len(MEM), len(keep)))
    print('=== LOOK: fresh interview-antagonism A matrix | n=%d ===' % len(keep))
    primary, own = eval_bars(predA, yA, core, crit, mask, Z, 'primary')
    robust = False
    if primary:
        robust = eval_bars(oof_all(Xst, cy, cmask, RESEEDS), yA, core, crit, mask, Z, 'fresh-seed')[0]
    Xno, km2 = AD.member_matrix('st_mean', MEM, genres=('CREC', 'social'))
    if km2 != MEM: raise RuntimeError('no-interview st_mean coverage/order drift')
    pno = oof_all(Xno, cy, cmask, BI.SEEDS)
    rno, pno_perm, _ = BI.exact_perm_p(pno[mask], yA)
    ship = rno > 0 and pno_perm < .05 and rno >= own / 2
    print('  no-interview ship gate: r=%+.3f p=%.4f half-primary=%s -> %s' %
          (rno, pno_perm, rno >= own / 2, 'PASS' if ship else 'FAIL'))
    print('=== VERDICT: %s ===' % ('A PASS — criterion-valid, distinct, and no-interview robust'
          if primary and robust and ship else 'A CONDITIONAL PASS — shared-source only; no production promotion'
          if primary and robust else 'A MISS — route closed'))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--criterion', action='store_true'); ap.add_argument('--look', action='store_true')
    args = ap.parse_args()
    if args.criterion: criterion_report()
    elif args.look: look()
    else: print('pass --criterion or --look')

if __name__ == '__main__':
    main()
