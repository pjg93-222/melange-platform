# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""ROUND-5 SUCCESSOR-GATE WORK (big5_a_matrix_prereg.md, Addendum 1) — multi-basis stratum-power
diagnostic. SYNTHETIC ONLY, NO LOOK: no real trait x criterion pairing is ever computed;
coop_v2_candidate.json is read ONLY for its coverage mask (criterion VALUES never meet a
prediction), and behavioral criteria are never loaded at all. Question: can any text basis recover
the Addendum-1 planted true r=.40 signal on the actual MEM_A stratum (same constructor
NM.plant_set, same five pinned seeds, bars 1-4) where supervised Empath could not (median +0.132,
2/5 — REPRODUCED EXACTLY by this script's first run, 2026-07-09)? Levers tested = exactly the two
the Addendum names: corpus growth (the interview corpus 3.8x'd on 07-09, AFTER the 07-07 emb
cache) and richer text representation (open-vocab LSA / sentence-transformer bases, rebuilt fresh
to b5_emb_cache_r6_*.npz).
Selection-effect guard, pinned pre-run: bases are screened on the five PINNED Addendum seeds; any
basis reaching >=3/5 on bars 1-4 must CONFIRM >=3/5 on five FRESH seeds (below) never used for
selection. A confirmed pass licenses ONLY a successor-prereg PROPOSAL to Paul (new instrument =
new prereg + adversarial review, per the lock's instrument-of-record language); it changes no
status. No LLM calls. No audio.
PARALLEL ORCHESTRATION (numerics unchanged — same seeds, same estimators, same constructor;
GridSearchCV n_jobs only parallelizes identical fits): --build lsa|st builds one cache group;
--basis NAME sweeps one basis (own process); --fresh switches to the confirmation seeds.
  python fec/members/b5_a_stratum_diag.py --build lsa          (writes b5_emb_cache_r6_lsa.npz)
  python fec/members/b5_a_stratum_diag.py --build st           (writes b5_emb_cache_r6_st.npz)
  python fec/members/b5_a_stratum_diag.py --basis lsa256 --jobs 2 --out r.json
  python fec/members/b5_a_stratum_diag.py --basis empath+lsa256 --fresh --jobs 2 --out r.json"""
import json, re, sqlite3, collections, argparse
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, GridSearchCV
_s.stdout.reconfigure(encoding='utf-8', errors='replace')
import members.b5_behavioral_instrument as BI      # import-safe (__main__-guarded): featurize/exact_perm_p/SEEDS/ALPHAS
import members.b5_n_matrix as NM                   # import-safe: rr/zz/plant_set (Addendum-1 orthogonalized constructor)
import moneyball_core as MC
B = MB_ROOT
EMB = {'lsa': (B + r'\data\members\b5_emb_cache_r6_lsa.npz', B + r'\data\members\b5_emb_meta_r6_lsa.json'),
       'st':  (B + r'\data\members\b5_emb_cache_r6_st.npz',  B + r'\data\members\b5_emb_meta_r6_st.json')}
PIN5 = [424242, 515151, 616161, 717171, 818181]    # the Addendum-1 diagnostic seeds (selection set)
FRESH5 = [131313, 272727, 393939, 515253, 676767]  # confirmation set — pinned here, never used for selection
GENRES = ('CREC', 'INTERVIEW', 'social')
CORE = NM.CORE
rr, zz = NM.rr, NM.zz
JOBS = [1]                                          # GridSearchCV n_jobs (identical fits, parallel)

def oof(X, y):
    """BI.oof verbatim (same SEEDS/ALPHAS/KFold/GridSearchCV recipe) + n_jobs — numerically
    identical, the parallelism only distributes the same deterministic fits."""
    pr = np.zeros((len(BI.SEEDS), len(y)))
    for i, s in enumerate(BI.SEEDS):
        for tr, te in KFold(5, shuffle=True, random_state=s).split(X):
            g = GridSearchCV(Ridge(), {'alpha': BI.ALPHAS}, cv=3, scoring='neg_mean_squared_error', n_jobs=JOBS[0])
            g.fit(X[tr], y[tr]); pr[i, te] = g.predict(X[te])
    return pr.mean(0)

# ---------------------------------------------------------------- emb rebuild (current corpus)
RITUAL = re.compile(r'\b(i yield (back|to|the floor|myself)[^.]*|mr\.? (president|speaker)|madam (president|speaker)|'
 r'i ask unanimous consent[^.]*|the (gentleman|gentlewoman|gentlelady) from [a-z ]+|'
 r'i (rise|reserve the balance of my time)|pursuant to [^.]*|the clerk will [^.]*|'
 r'i suggest the absence of a quorum|the balance of my time)\b', re.I)

def load_docs():
    """b5_emb_build.py corpus prep verbatim (that file is import==execution, so replicated)."""
    b2n = MC.bio2name()
    con = sqlite3.connect(B + r'\social_media.db', timeout=30); con.execute('PRAGMA query_only=1')
    docs = collections.defaultdict(lambda: {'text': '', 'tok': 0})
    for g in ('CREC', 'INTERVIEW'):
        for m, txt in con.execute("select member, group_concat(text,' ') from speeches where source=? group by member", (g,)):
            t = RITUAL.sub(' ', txt or ''); docs[(m, g)]['text'] = t; docs[(m, g)]['tok'] = len(t.split())
    soc = collections.defaultdict(list)
    for bg, txt in con.execute("select bioguide, group_concat(text,' ') from posts where repost_of is null group by bioguide"):
        m = b2n.get(bg)
        if m: soc[m].append(txt or '')
    for m, ts in soc.items():
        t = ' '.join(ts); docs[(m, 'social')]['text'] = t; docs[(m, 'social')]['tok'] = len(t.split())
    con.close()
    pairs = [(m, g) for (m, g), d in docs.items() if d['tok'] >= 50]
    print('corpus prep: %d members, %d member-genre docs' % (len({m for m, g in pairs}), len(pairs)), flush=True)
    return docs, pairs

def write_group(group, feats, pairs, toks):
    npzf, metaf = EMB[group]
    bases = list(feats)
    tmpnpz = npzf + '.tmp'    # file handle (not a name) so numpy does not append .npz; both files appear only once complete
    with open(tmpnpz, 'wb') as fh:
        np.savez_compressed(fh, **{'%s|%s|%s' % (b, m, g): feats[b][(m, g)]
                                   for b in bases for (m, g) in pairs if (m, g) in feats[b]})
    _o.replace(tmpnpz, npzf)
    MC.dump_atomic({'bases': bases, 'members': sorted({m for m, g in pairs}),
                    'pairs': ['%s|%s' % (m, g) for (m, g) in pairs],
                    'toks': {'%s|%s' % (m, g): toks[(m, g)] for (m, g) in pairs}}, metaf)
    print('wrote %s (%s)' % (npzf, ', '.join(bases)), flush=True)

def build(group):
    npzf, metaf = EMB[group]
    if _o.path.exists(npzf) and _o.path.exists(metaf):
        print('%s cache present — reusing' % group, flush=True); return
    docs, pairs = load_docs()
    toks = {(m, g): docs[(m, g)]['tok'] for (m, g) in pairs}
    feats = {}
    if group == 'lsa':
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.decomposition import TruncatedSVD
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import Normalizer
        corpus = [docs[(m, g)]['text'] for (m, g) in pairs]
        for K in (128, 256, 512):
            pipe = make_pipeline(
                TfidfVectorizer(ngram_range=(1, 2), min_df=3, max_features=40000, sublinear_tf=True),
                TruncatedSVD(n_components=K, random_state=7),
                Normalizer(copy=False))
            X = pipe.fit_transform(corpus)
            feats['lsa%d' % K] = {pairs[i]: X[i] for i in range(len(pairs))}
            print('  lsa%d: %s' % (K, X.shape), flush=True)
    else:
        from sentence_transformers import SentenceTransformer
        st = SentenceTransformer('all-MiniLM-L6-v2')
        def chunks(text, w=256):
            ws = text.split()
            return [' '.join(ws[i:i + w]) for i in range(0, min(len(ws), 256 * 40), w)] or ['']
        stmean = {}
        for i, (m, g) in enumerate(pairs):
            cs = chunks(docs[(m, g)]['text'])
            emb = st.encode(cs, batch_size=64, show_progress_bar=False, normalize_embeddings=True)
            stmean[(m, g)] = np.asarray(emb).mean(axis=0)
            if i % 100 == 0: print('  st encoding %d/%d' % (i, len(pairs)), flush=True)
        feats['st_mean'] = stmean
        from sklearn.linear_model import LinearRegression
        volctrl = {}
        for g in GENRES:
            gp = [(m, gg) for (m, gg) in pairs if gg == g]
            if len(gp) < 20:
                for k in gp: volctrl[k] = stmean[k]
                continue
            Xg = np.array([stmean[k] for k in gp]); lv = np.array([[np.log1p(toks[k])] for k in gp])
            res = Xg - LinearRegression().fit(lv, Xg).predict(lv)
            for j, k in enumerate(gp): volctrl[k] = res[j]
        feats['st_volctrl'] = volctrl
        print('  st bases built', flush=True)
    write_group(group, feats, pairs, toks)

def member_matrix(base, MEM, genres=GENRES):
    """b5_round4_eval composition verbatim: within-genre z, token-weighted cross-genre average."""
    group = 'lsa' if base.startswith('lsa') else 'st'
    npzf, metaf = EMB[group]
    meta = json.load(open(metaf))
    npz = np.load(npzf)
    TOK = {tuple(k.split('|')): v for k, v in meta['toks'].items()}
    pairs = [tuple(p.split('|')) for p in meta['pairs']]
    Z = {}
    for g in genres:
        ms = [m for (m, gg) in pairs if gg == g and ('%s|%s|%s' % (base, m, g)) in npz.files]
        if len(ms) < 20: continue
        X = np.array([npz['%s|%s|%s' % (base, m, g)] for m in ms])
        mu, sd = X.mean(0), X.std(0, ddof=1); sd[sd == 0] = 1
        Z[g] = ({m: i for i, m in enumerate(ms)}, (X - mu) / sd)
    rows, keep = [], []
    for m in MEM:
        num = None; den = 0.0
        for g in Z:
            idx, Xg = Z[g]
            if m in idx:
                w = TOK.get((m, g), 1)
                num = Xg[idx[m]] * w if num is None else num + Xg[idx[m]] * w; den += w
        if den > 0: rows.append(num / den); keep.append(m)
    return np.array(rows), keep

# ---------------------------------------------------------------- the diagnostic
def diag(name, X, mask, seeds):
    """Addendum-1 stratum-power diagnostic, verbatim semantics, parameterized by basis. Per seed:
    orthogonalized plant set (NM.plant_set, target r=.40), masked-A plant on the ACTUAL stratum;
    bars 1-4 = own>=.15 | exact perm-p<.05 | row-dominant vs O/C/E/ES plants | column-specific
    strict vs all five core-plant models. Entirely synthetic."""
    Xs = X[mask]
    out = []
    for sd in seeds:
        rng = np.random.default_rng(sd)
        yc, yn = NM.plant_set(X, Xs, mask, rng)
        pc = {t: oof(X, yc[t]) for t in CORE}
        pn = oof(Xs, yn)
        own, pp, _ = BI.exact_perm_p(pn, yn)
        row = all(abs(own) >= abs(rr(pn, yc[c][mask])) for c in ['O', 'C', 'E', 'ES']) and own > 0
        col = all(abs(own) > abs(rr(pc[c][mask], yn)) for c in CORE)
        b12 = (own >= 0.15) and (pp < .05)
        b14 = b12 and row and col
        out.append({'seed': sd, 'own': own, 'perm_p': pp, 'row': row, 'col': col, 'b12': b12, 'b14': b14})
        print('  [%s] seed %d: own %+.3f p %.4f row %s col %s -> bars1-4 %s'
              % (name, sd, own, pp, row, col, 'PASS' if b14 else 'miss'), flush=True)
    med = float(np.median([o['own'] for o in out]))
    n12 = sum(o['b12'] for o in out); n14 = sum(o['b14'] for o in out)
    gate = n14 >= 3
    print('  [%s] SUMMARY: median own %+.3f | bars1-2 %d/5 | bars1-4 %d/5 -> gate %s'
          % (name, med, n12, n14, 'CLEARED' if gate else 'refused'), flush=True)
    return {'basis': name, 'median_own': med, 'n12': n12, 'n14': n14, 'gate': gate, 'runs': out}

def basis_matrix(name, MEM, alltext, mask):
    """Feature matrix + sliced stratum mask for a named basis."""
    if name == 'empath':
        return BI.featurize(MEM, alltext), mask, len(MEM)
    if name == 'empath+lsa256':
        Xe = BI.featurize(MEM, alltext)
        Xb, keep = member_matrix('lsa256', MEM)
        ki = [MEM.index(m) for m in keep]
        Xc = np.hstack([np.apply_along_axis(zz, 0, Xe[ki]), np.apply_along_axis(zz, 0, Xb)])
        return Xc, mask[ki], len(keep)
    Xb, keep = member_matrix(name, MEM)
    return Xb, mask[[MEM.index(m) for m in keep]], len(keep)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', choices=['lsa', 'st'], help='build one cache group and exit')
    ap.add_argument('--basis', help='sweep one basis: empath | lsa128 | lsa256 | lsa512 | st_mean | st_volctrl | empath+lsa256')
    ap.add_argument('--criterion', choices=['coop_v2', 'interview_antagonism'], default='coop_v2',
                    help='coverage mask only; criterion values are never loaded')
    ap.add_argument('--fresh', action='store_true', help='use the pinned FRESH confirmation seeds')
    ap.add_argument('--jobs', type=int, default=1, help='GridSearchCV n_jobs (identical numerics)')
    ap.add_argument('--out', default='')
    args = ap.parse_args()
    JOBS[0] = args.jobs
    if args.build:
        build(args.build); return
    if not args.basis:
        print('nothing to do: pass --build lsa|st or --basis NAME'); return
    seeds = FRESH5 if args.fresh else PIN5
    print('round-5 successor-gate diagnostic (SYNTHETIC ONLY — no look, no real pairing)', flush=True)
    print('basis=%s criterion-mask=%s seeds=%s jobs=%d' %
          (args.basis, args.criterion, 'FRESH5 (confirmation)' if args.fresh else 'PIN5 (Addendum-1)', args.jobs), flush=True)
    BD, VV, P, MEM, alltext = BI.load_corpus()
    if args.criterion == 'coop_v2':
        CV = json.load(open(B + r'\coop_v2_candidate.json'))['members']
        covered = set(m for m, v in CV.items() if (v or {}).get('cooperation_v2_z') is not None)
        del CV
    else:
        from members.interview_antagonism_score import load_halves
        covered = set(load_halves())
    mask = np.array([m in covered for m in MEM])
    print('rosters: MEM=%d | MEM_A=%d | mask=%s' %
          (len(MEM), int(mask.sum()), args.criterion), flush=True)
    X, kmask, n = basis_matrix(args.basis, MEM, alltext, mask)
    print('--- basis: %s (n=%d, stratum n=%d) ---' % (args.basis, n, int(kmask.sum())), flush=True)
    r = diag(args.basis + (' [FRESH]' if args.fresh else ''), X, kmask, seeds)
    if args.out:
        MC.dump_atomic(r, args.out, indent=1)
        print('wrote %s' % args.out, flush=True)

if __name__ == '__main__':
    main()
