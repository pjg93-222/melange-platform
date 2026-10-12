# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""BEMB feature builder (big5_round4_prereg.md @d59bde5). Computes per-member per-genre document
feature matrices for every candidate open-vocabulary basis, once, to a cache. round4_eval.py then
does the split-sample selection + one-look test + CT-CM. Feature bases (all OPEN-vocab, no pink):
  lsa128 / lsa256 / lsa512 : TF-IDF (word 1-2gram + char 3-5gram) -> TruncatedSVD -> L2-normalize
  st_mean                  : sentence-transformer (all-MiniLM-L6-v2) chunk embeddings, mean-pooled
  st_volctrl               : st_mean residualized on log(tokens) per genre (kills E-volume common source)
Per genre, features are computed from that genre's text only; standardization-within-genre and the
cross-genre token-weighted average happen in round4_eval (so MTMM can use the per-genre matrices).
Writes b5_emb_cache.npz + b5_emb_meta.json to the scratchpad-adjacent repo tmp (gitignored).
Usage: python fec/members/b5_emb_build.py            (LSA always; ST only if sentence-transformers importable)"""
import json, re, sqlite3, collections
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import Normalizer
B = MB_ROOT
OUT = B + r'\data\members\b5_emb_cache.npz'
GENRES = ('CREC', 'INTERVIEW', 'social')
RITUAL = re.compile(r'\b(i yield (back|to|the floor|myself)[^.]*|mr\.? (president|speaker)|madam (president|speaker)|'
 r'i ask unanimous consent[^.]*|the (gentleman|gentlewoman|gentlelady) from [a-z ]+|'
 r'i (rise|reserve the balance of my time)|pursuant to [^.]*|the clerk will [^.]*|'
 r'i suggest the absence of a quorum|the balance of my time)\b', re.I)

import moneyball_core as MC
b2n = MC.bio2name()
con = sqlite3.connect(B + r'\social_media.db', timeout=30); con.execute('PRAGMA query_only=1')
# per (member, genre): concatenated cleaned text + token count
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

# member x genre presence (require >= 50 tokens to feature a genre)
pairs = [(m, g) for (m, g), d in docs.items() if d['tok'] >= 50]
members = sorted({m for m, g in pairs})
print('BEMB build: %d members, %d member-genre docs' % (len(members), len(pairs)))

feats = {}   # base_name -> {(member,genre): vector}
toks = {(m, g): docs[(m, g)]['tok'] for (m, g) in pairs}

# ---- LSA bases: fit TF-IDF+SVD on ALL docs (unsupervised, no labels -> no leakage) ----
corpus = [docs[(m, g)]['text'] for (m, g) in pairs]
for K in (128, 256, 512):
    pipe = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=3, max_features=40000, sublinear_tf=True),
        TruncatedSVD(n_components=K, random_state=7),
        Normalizer(copy=False))
    X = pipe.fit_transform(corpus)
    feats['lsa%d' % K] = {pairs[i]: X[i] for i in range(len(pairs))}
    print('  lsa%d: %s' % (K, X.shape))

# ---- sentence-transformer bases (only if importable) ----
try:
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
        if i % 200 == 0: print('  st encoding %d/%d' % (i, len(pairs)), flush=True)
    feats['st_mean'] = stmean
    print('  st_mean: dim %d' % len(next(iter(stmean.values()))))
    # volume-controlled: residualize each st dim on log(tokens) WITHIN genre
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
    print('  st_volctrl: built')
except Exception as e:
    print('  sentence-transformers unavailable (%s) — LSA bases only' % str(e)[:70])

# ---- save ----
bases = list(feats)
np.savez_compressed(OUT, **{'%s|%s|%s' % (b, m, g): feats[b][(m, g)]
                            for b in bases for (m, g) in pairs if (m, g) in feats[b]})
json.dump({'bases': bases, 'members': members,
           'pairs': ['%s|%s' % (m, g) for (m, g) in pairs],
           'toks': {'%s|%s' % (m, g): toks[(m, g)] for (m, g) in pairs}},
          open(B + r'\data\members\b5_emb_meta.json', 'w'))
print('wrote b5_emb_cache.npz (%d bases: %s) + b5_emb_meta.json' % (len(bases), ', '.join(bases)))
