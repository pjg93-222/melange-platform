# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT
"""PRE-REGISTERED improvement-round evaluator (big5_improvements_prereg.md @a61820e, bars locked
2026-07-06 BEFORE this data existed). ONE look per mode on the FROZEN snapshot
(big5_snapshot_freeze.json, 2026-07-07 19:26: 4,217 chunks, 509 members, artifact md5 67af4451...).

Modes (run each ONCE, in the declared order):
  --control  equal-weight configuration on the frozen snapshot (descriptive control; not a look)
  --p1       item discrimination weights          (miss -> equal weights stand)
  --p2       bifactor halo removal                (miss -> halo stays, caveat documented)
  --p4       empirical-Bayes shrinkage on EQ base (miss -> raw stands, k ships as info)
  --pall     composition of the individually-passing P-changes (final gate)

Evaluation law: P1/P2/P4 are each evaluated INDEPENDENTLY against the ORIGINAL prereg baselines
(never against each other); 'improves on >=N traits' bars use the equal-weight CONTROL computed on
the SAME frozen snapshot (corpus held constant), while absolute bars use the prereg's locked
numbers. Chunk law, itemset, keying, and cache keys are copied LOCKSTEP from big5_llm.py (cannot
import it: import==execution)."""
import json, re, math, hashlib, sqlite3, collections, sys
import numpy as np

B = MB_ROOT
_mk = 'opus'
CHUNK_WORDS, CHUNKS_PER_GENRE, MIN_WORDS = 1200, 6, 2000
GENRES = ('CREC', 'INTERVIEW')
TR = ['O', 'C', 'E', 'A', 'ES']
ITEMS = [
 ('O1', +1, 'O'), ('O2', +1, 'O'), ('O3', -1, 'O'), ('O4', +1, 'O'),
 ('C1', +1, 'C'), ('C2', +1, 'C'), ('C3', -1, 'C'), ('C4', +1, 'C'),
 ('E1', +1, 'E'), ('E2', +1, 'E'), ('E3', -1, 'E'), ('E4', +1, 'E'),
 ('A1', +1, 'A'), ('A2', +1, 'A'), ('A3', -1, 'A'), ('A4', +1, 'A'),
 ('S1', +1, 'ES'), ('S2', -1, 'ES'), ('S3', -1, 'ES'), ('S4', +1, 'ES')]
IDX = {i: k for k, (i, s, t) in enumerate(ITEMS)}
KEY_SIGN = np.array([s for _, s, _ in ITEMS], dtype=float)
TRAIT_COLS = {t: [k for k, (_, _, tt) in enumerate(ITEMS) if tt == t] for t in TR}
EXPECT_R_SIGN = {'O': -1, 'C': +1, 'E': -1, 'A': -1, 'ES': +1}

# ---- lockstep with big5_llm.py: itemset version + ritual scrub + de-identification + chunking ----
import importlib.util
_spec_src = open(_o.path.join(B, 'fec', 'members', 'big5_llm.py'), encoding='utf-8').read()
ITEMSET_V = re.search(r"ITEMSET_V\s*=\s*'([^']+)'", _spec_src).group(1)
RITUAL = re.compile(r'\b(i yield (back|to|the floor|myself)[^.]*|mr\.? (president|speaker)|madam (president|speaker)|'
 r'i ask unanimous consent[^.]*|the (gentleman|gentlewoman|gentlelady) from [a-z ]+|'
 r'i (rise|reserve the balance of my time)|pursuant to [^.]*|the clerk will [^.]*|'
 r'i suggest the absence of a quorum|the balance of my time)\b', re.I)
STATES = ("alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho "
          "illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota "
          "mississippi missouri montana nebraska nevada ohio oklahoma oregon pennsylvania tennessee texas utah "
          "vermont virginia washington wisconsin wyoming").split() + \
         ["new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota",
          "rhode island", "south carolina", "south dakota", "west virginia"]
TITLE_RE = re.compile(r'\b(senator|congressman|congresswoman|representative|rep\.|sen\.|chairman|chairwoman|'
                      r'ranking member|speaker|leader|whip)\b', re.I)
PARTY_RE = re.compile(r'\b(republican[s]?|democrat[s]?|democratic|gop)\b', re.I)
def deidentify(text, member):
    parts = [p for p in re.split(r'\s+', member) if len(p) > 2]
    for p in parts: text = re.sub(re.escape(p), '[NAME]', text, flags=re.I)
    text = TITLE_RE.sub('[TITLE]', text)
    text = PARTY_RE.sub('[PARTY]', text)
    for st in sorted(STATES, key=len, reverse=True):
        text = re.sub(r'\b' + re.escape(st) + r'\b', '[STATE]', text, flags=re.I)
    return text
def chunks_for(member, genre, con):
    docs = [t for (t,) in con.execute(
        'select text from speeches where member=? and source=? order by date, rowid', (member, genre)) if t]
    words = []
    for t in docs:
        t = RITUAL.sub(' ', t)
        w = t.split()
        if len(w) >= 30: words.extend(w)
    if len(words) < MIN_WORDS: return []
    total = len(words)
    n = min(CHUNKS_PER_GENRE, total // CHUNK_WORDS)
    if n == 0: return []
    out = []
    for j in range(n):
        start = int(j * (total - CHUNK_WORDS) / max(1, n - 1)) if n > 1 else 0
        out.append(' '.join(words[start:start + CHUNK_WORDS]))
    return out

print('loading frozen snapshot ...')
C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
con = sqlite3.connect(B + r'\social_media.db', timeout=30); con.execute('PRAGMA query_only=1')
rows = []   # (member, genre, chunk_idx, key)
for m in C:
    for g in GENRES:
        for ci, ch in enumerate(chunks_for(m, g, con)):
            txt = deidentify(ch, m)
            th = hashlib.md5(txt.encode('utf-8', 'replace')).hexdigest()[:16]
            k = hashlib.md5(('%s|%s|%d|%s|%s|%s%s|%s' % (m, g, ci, 'dei', ITEMSET_V, _mk, '', th)).encode()).hexdigest()
            rows.append((m, g, ci, k))
con.close()
cache = {}
for ln in open(B + r'\data\members\big5_llm_cache_%s.jsonl' % _mk, encoding='utf-8'):
    try:
        d = json.loads(ln); cache[d['k']] = d['out']
    except Exception: pass
data = []   # (member, genre, ci, keyed 20-vector)
for m, g, ci, k in rows:
    o = cache.get(k)
    if not o or not isinstance(o, dict) or len(o) < 15: continue
    v = np.array([o.get(i, 0) for i, _, _ in ITEMS], dtype=float) * KEY_SIGN
    data.append((m, g, ci, v))
X = np.array([v for _, _, _, v in data])            # chunks x 20, keyed
meta = [(m, g, ci) for m, g, ci, _ in data]
print('  matched chunks: %d (of %d rebuilt keys) | members: %d' % (
    len(data), len(rows), len({m for m, _, _ in meta})))

# ---- shared aggregation: chunk trait scores -> genre mean -> member (chunk-weighted) -> z ----
def aggregate(ct, only=None):   # ct: chunks x 5 traits -> member z + per-genre scores + halves; only = member subset
    gm = collections.defaultdict(lambda: collections.defaultdict(list))
    for (m, g, ci), row in zip(meta, ct):
        if only is not None and m not in only: continue
        gm[m][g].append((ci, row))
    member_raw, genre_scores, half = {}, collections.defaultdict(dict), {0: {}, 1: {}}
    nchunks = {}
    for m, gs in gm.items():
        pg, wsum = {}, 0
        for g, cl in gs.items():
            arr = np.array([r for _, r in cl])
            pg[g] = arr.mean(axis=0); genre_scores[m][g] = pg[g]
        wsum = sum(len(gs[g]) for g in pg)
        member_raw[m] = sum(pg[g] * len(gs[g]) for g in pg) / wsum
        nchunks[m] = wsum
        for par in (0, 1):   # split-half: even/odd chunk index within member+genre
            pgh = {g: np.array([r for ci, r in cl if ci % 2 == par]) for g, cl in gs.items()}
            pgh = {g: a for g, a in pgh.items() if len(a)}
            if pgh:
                w = sum(len(a) for a in pgh.values())
                half[par][m] = sum(a.mean(axis=0) * len(a) for a in pgh.values()) / w
    names = sorted(member_raw)
    M = np.array([member_raw[m] for m in names])
    mu, sd = M.mean(axis=0), M.std(axis=0, ddof=1)
    sd[sd == 0] = 1.0
    Z = (M - mu) / sd
    return names, Z, member_raw, genre_scores, half, nchunks

def pearson(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or a.std() == 0 or b.std() == 0: return 0.0
    return float(np.corrcoef(a, b)[0, 1])
def spearman(pairs):
    if len(pairs) < 3: return None, len(pairs)
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i]); rk = [0.0] * len(v); i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]: j += 1
            r = (i + j) / 2 + 1
            for kk in range(i, j + 1): rk[order[kk]] = r
            i = j + 1
        return rk
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    return pearson(ranks(xs), ranks(ys)), len(pairs)

def metrics(ct, label, only=None):
    names, Z, raw, gsc, half, nch = aggregate(ct, only=only)
    zidx = {m: i for i, m in enumerate(names)}
    out = {'label': label}
    # split-half SB per trait (across members present in both halves)
    both = [m for m in names if m in half[0] and m in half[1]]
    sb = {}
    for ti, t in enumerate(TR):
        r = pearson([half[0][m][ti] for m in both], [half[1][m][ti] for m in both])
        sb[t] = 2 * r / (1 + r) if r > -1 else -1
    out['sb'] = sb; out['sb_mean'] = sum(sb.values()) / 5
    # MTMM same-trait cross-genre (CREC x INTERVIEW)
    bothg = [m for m in names if len(gsc[m]) == 2]
    mt = {t: pearson([gsc[m]['CREC'][ti] for m in bothg], [gsc[m]['INTERVIEW'][ti] for m in bothg])
          for ti, t in enumerate(TR)}
    out['mtmm'] = mt; out['mtmm_mean'] = sum(mt.values()) / 5
    # discriminant
    offs = [abs(pearson(Z[:, i], Z[:, j])) for i in range(5) for j in range(i + 1, 5)]
    out['disc_mean'] = sum(offs) / len(offs)
    out['a_es'] = abs(pearson(Z[:, TR.index('A')], Z[:, TR.index('ES')]))
    # known-groups (party from moneyball_core.party(): 'R'/'D' coded — congestion's own key is not)
    import moneyball_core as _MC
    P = _MC.party()
    kg = 0
    for ti, t in enumerate(TR):
        rs = [Z[zidx[m]][ti] for m in names if P.get(m) == 'R']
        ds = [Z[zidx[m]][ti] for m in names if P.get(m) == 'D']
        if rs and ds and ((sum(rs)/len(rs) - sum(ds)/len(ds)) > 0) == (EXPECT_R_SIGN[t] > 0): kg += 1
    out['kg'] = kg
    # C criterion (interview-genre C vs votes_cast + followthrough)
    PAN = json.load(open(B + r'\behavior_panel.json', encoding='utf-8'))['members']
    LEP = json.load(open(B + r'\data\members\lep_criterion.json', encoding='utf-8'))['per_member']
    ci_ = TR.index('C')
    cvals = {m: gsc[m]['INTERVIEW'][ci_] for m in names if 'INTERVIEW' in gsc[m]}
    rv, nv = spearman([(cvals[m], PAN[m]['votes_cast']) for m in cvals
                       if PAN.get(m, {}).get('votes_cast') is not None])
    rf, nf = spearman([(cvals[m], LEP[m]['followthrough']) for m in cvals
                       if (LEP.get(m) or {}).get('followthrough') is not None])
    out['c_votes'] = (rv, nv); out['c_ft'] = (rf, nf)
    print('\n===== %s =====' % label)
    print('  split-half SB: ' + ' '.join('%s %.3f' % (t, sb[t]) for t in TR) + '  | mean %.3f' % out['sb_mean'])
    print('  MTMM same-trait cross-genre: ' + ' '.join('%s %.3f' % (t, mt[t]) for t in TR) + '  | mean %.3f' % out['mtmm_mean'])
    print('  discriminant mean off-diag |r|: %.3f | A-ES |r|: %.3f' % (out['disc_mean'], out['a_es']))
    print('  known-groups: %d/5 | C(INT) vs votes %s (n=%d) vs FT %s (n=%d)' % (
        kg, '%+.3f' % rv if rv is not None else 'n/a', nv, '%+.3f' % rf if rf is not None else 'n/a', nf))
    return out, (names, Z, raw, gsc, half, nch)

# ---- configurations ----
def ct_equal():
    return np.stack([X[:, TRAIT_COLS[t]].mean(axis=1) for t in TR], axis=1)
def p1_weights():
    W = {}
    for t in TR:
        cols = TRAIT_COLS[t]; w = []
        for c in cols:
            others = [cc for cc in cols if cc != c]
            w.append(abs(pearson(X[:, c], X[:, others].sum(axis=1))))
        w = np.array(w)
        w = w / w.sum() if w.sum() > 0 else np.full(len(cols), .25)
        w = np.clip(w, 0.10, 0.60); w = w / w.sum()
        W[t] = w
    return W
def ct_p1(W):
    return np.stack([X[:, TRAIT_COLS[t]] @ W[t] for t in TR], axis=1)
def ct_p2():
    Xc = X - X.mean(axis=0)
    U, S, Vt = np.linalg.svd(Xc, full_matrices=False)
    v = Vt[0]                       # first principal component = general evaluative/halo factor
    R = Xc - np.outer(Xc @ v, v)    # residuals after removing PC1 (same projection every chunk)
    return np.stack([R[:, TRAIT_COLS[t]].mean(axis=1) for t in TR], axis=1)

mode = sys.argv[1] if len(sys.argv) > 1 else '--control'
if mode == '--control':
    metrics(ct_equal(), 'CONTROL: equal weights on frozen snapshot')
elif mode == '--p1':
    W = p1_weights()
    print('P1 weights: ' + ' | '.join('%s [%s]' % (t, ' '.join('%.2f' % x for x in W[t])) for t in TR))
    ctl, _ = metrics(ct_equal(), 'control (for per-trait comparison)')
    p1, _ = metrics(ct_p1(W), 'P1: discrimination-weighted items')
    improved = sum(1 for t in TR if p1['sb'][t] > ctl['sb'][t])
    bars = [('SB improves on >=3/5 traits (vs control)', improved >= 3, '%d/5' % improved),
            ('mean SB >= 0.704 (.674+.03)', p1['sb_mean'] >= 0.704, '%.3f' % p1['sb_mean']),
            ('MTMM mean >= 0.390', p1['mtmm_mean'] >= 0.390, '%.3f' % p1['mtmm_mean']),
            ('discriminant <= 0.30', p1['disc_mean'] <= 0.30, '%.3f' % p1['disc_mean'])]
    ok = all(b[1] for b in bars)
    for nm, hit, d in bars: print('  BAR %-42s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', d))
    print('P1 VERDICT: %s' % ('PASS — promote discrimination weights' if ok else 'MISS — equal weights stand'))
elif mode == '--p2':
    p2, _ = metrics(ct_p2(), 'P2: bifactor halo removed (PC1 residuals)')
    bars = [('A-ES |r| <= 0.50', p2['a_es'] <= 0.50, '%.3f' % p2['a_es']),
            ('MTMM mean >= 0.390', p2['mtmm_mean'] >= 0.390, '%.3f' % p2['mtmm_mean']),
            ('known-groups >= 4/5', p2['kg'] >= 4, '%d/5' % p2['kg']),
            ('C vs votes >= 0.126 (.146-.02)', (p2['c_votes'][0] or -9) >= 0.126, '%+.3f' % (p2['c_votes'][0] or -9)),
            ('C vs followthrough >= 0.112 (.132-.02)', (p2['c_ft'][0] or -9) >= 0.112, '%+.3f' % (p2['c_ft'][0] or -9))]
    ok = all(b[1] for b in bars)
    for nm, hit, d in bars: print('  BAR %-42s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', d))
    print('P2 VERDICT: %s' % ('PASS — promote halo removal' if ok else 'MISS — halo stays (documented caveat)'))
elif mode == '--p4':
    ctl, (names, Z, raw, gsc, half, nch) = metrics(ct_equal(), 'EQ base (configuration under test)')
    ct = ct_equal()
    # method-of-moments k per trait: sigma2_within (pooled chunk variance within member) /
    # sigma2_between (variance of member means minus within/nbar)
    gm = collections.defaultdict(list)
    for (m, g, ci), row in zip(meta, ct): gm[m].append(row)
    ks = {}
    for ti, t in enumerate(TR):
        w_list, means, ns = [], [], []
        for m, rws in gm.items():
            a = np.array([r[ti] for r in rws])
            if len(a) >= 2: w_list.append(a.var(ddof=1))
            means.append(a.mean()); ns.append(len(a))
        s2w = float(np.mean(w_list)); nbar = float(np.mean(ns))
        s2b = max(1e-9, float(np.var(means, ddof=1)) - s2w / nbar)
        ks[t] = s2w / s2b
    print('  k per trait: ' + ' '.join('%s %.1f' % (t, ks[t]) for t in TR))
    zidx = {m: i for i, m in enumerate(names)}
    # shrunken member scores + shrunken halves (each half with its own n)
    def shrink_pack(halfdict, nof):
        return {m: np.array([v[ti] * (nof(m) / (nof(m) + ks[t])) for ti, t in enumerate(TR)])
                for m, v in halfdict.items()}
    nch_h = {par: collections.Counter() for par in (0, 1)}
    for (m, g, ci) in meta: nch_h[ci % 2][m] += 1
    sh_half = {par: shrink_pack(half[par], lambda m, par=par: max(1, nch_h[par][m])) for par in (0, 1)}
    both = [m for m in names if m in sh_half[0] and m in sh_half[1]]
    sb = {}
    for ti, t in enumerate(TR):
        r = pearson([sh_half[0][m][ti] for m in both], [sh_half[1][m][ti] for m in both])
        sb[t] = 2 * r / (1 + r) if r > -1 else -1
    sb_mean = sum(sb.values()) / 5
    # movement bound on well-measured members: |z*(m) - z(m)| = |z|*k/(n+k) in SD units
    worst = max(abs(Z[zidx[m]][ti]) * ks[t] / (nch[m] + ks[t])
                for m in names if nch[m] >= 8 for ti, t in enumerate(TR))
    print('  P4 shrunken split-half SB: ' + ' '.join('%s %.3f' % (t, sb[t]) for t in TR) + ' | mean %.3f' % sb_mean)
    bars = [('mean SB >= EQ base + 0.02', sb_mean >= ctl['sb_mean'] + 0.02, '%.3f vs %.3f' % (sb_mean, ctl['sb_mean'])),
            ('no n>=8 member moves > 0.5 SD', worst <= 0.5, 'worst %.3f' % worst)]
    ok = all(b[1] for b in bars)
    for nm, hit, d in bars: print('  BAR %-42s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', d))
    print('P4 VERDICT: %s' % ('PASS — promote EB shrinkage' if ok else 'MISS — raw stands; k ships as info column'))
elif mode == '--p5':
    # ROUND 3 P5 (prereg @1967b15): split-sample A-ES item surgery. SELECT on even-md5 members,
    # TEST once on odd-md5 members. Drop at most one A item and one ES item.
    SEL = {m for m in C if int(hashlib.md5(m.encode()).hexdigest(), 16) % 2 == 0}
    TEST = {m for m in C if m not in SEL}
    print('P5 split: selection=%d members, test=%d members' % (len(SEL), len(TEST)))
    A_COLS, ES_COLS = TRAIT_COLS['A'], TRAIT_COLS['ES']
    A_IDS = [ITEMS[c][0] for c in A_COLS]; ES_IDS = [ITEMS[c][0] for c in ES_COLS]
    def ct_drop(dropA, dropES):
        cols = {t: list(TRAIT_COLS[t]) for t in TR}
        if dropA is not None: cols['A'] = [c for c in cols['A'] if c != dropA]
        if dropES is not None: cols['ES'] = [c for c in cols['ES'] if c != dropES]
        return np.stack([X[:, cols[t]].mean(axis=1) for t in TR], axis=1)
    def half_stats(ct, only):
        names, Z, raw, gsc, half, nch = aggregate(ct, only=only)
        both = [m for m in names if m in half[0] and m in half[1]]
        sb = {}
        for ti, t in enumerate(TR):
            r = pearson([half[0][m][ti] for m in both], [half[1][m][ti] for m in both])
            sb[t] = 2 * r / (1 + r) if r > -1 else -1
        aes = abs(pearson(Z[:, TR.index('A')], Z[:, TR.index('ES')]))
        return sb, aes
    # SELECTION half: control floors, then deterministic argmin over the 25 candidates
    sb0, _ = half_stats(ct_equal(), SEL)
    best = None
    for dA in [None] + A_COLS:
        for dE in [None] + ES_COLS:
            sb, aes = half_stats(ct_drop(dA, dE), SEL)
            feasible = sb['A'] >= sb0['A'] - 0.02 and sb['ES'] >= sb0['ES'] - 0.02
            ndrops = (dA is not None) + (dE is not None)
            key = (aes, ndrops, A_COLS.index(dA) if dA is not None else -1,
                   ES_COLS.index(dE) if dE is not None else -1)
            if feasible and (best is None or key < best[0]): best = (key, dA, dE, aes, sb)
    _, dA, dE, sel_aes, sel_sb = best
    print('P5 SELECTED on even-half: drop A item %s, drop ES item %s | selection-half A-ES %.3f' % (
        ITEMS[dA][0] if dA is not None else 'NONE', ITEMS[dE][0] if dE is not None else 'NONE', sel_aes))
    # TEST half, one look: chosen config vs test-half control
    ctl, _ = metrics(ct_equal(), 'TEST-half control (equal weights, all items)', only=TEST)
    p5, _ = metrics(ct_drop(dA, dE), 'P5: selected item drops (TEST half)', only=TEST)
    bars = [('A-ES |r| <= 0.50', p5['a_es'] <= 0.50, '%.3f' % p5['a_es']),
            ('discriminant <= 0.30', p5['disc_mean'] <= 0.30, '%.3f' % p5['disc_mean']),
            ('MTMM >= ctrl-0.02', p5['mtmm_mean'] >= ctl['mtmm_mean'] - 0.02, '%.3f vs %.3f' % (p5['mtmm_mean'], ctl['mtmm_mean'])),
            ('SB mean >= ctrl-0.02', p5['sb_mean'] >= ctl['sb_mean'] - 0.02, '%.3f vs %.3f' % (p5['sb_mean'], ctl['sb_mean'])),
            ('known-groups >= 4/5', p5['kg'] >= 4, '%d/5' % p5['kg']),
            ('C votes >= ctrl-0.03', (p5['c_votes'][0] or -9) >= (ctl['c_votes'][0] or 9) - 0.03,
             '%+.3f vs %+.3f' % (p5['c_votes'][0] or -9, ctl['c_votes'][0] or -9)),
            ('C followthrough >= ctrl-0.03', (p5['c_ft'][0] or -9) >= (ctl['c_ft'][0] or 9) - 0.03,
             '%+.3f vs %+.3f' % (p5['c_ft'][0] or -9, ctl['c_ft'][0] or -9))]
    ok = all(b[1] for b in bars)
    for nm, hit, det in bars: print('  BAR %-30s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', det))
    print('P5 VERDICT: %s' % ('PASS — promote item drops (%s, %s) to full-roster scoring' % (
        ITEMS[dA][0] if dA is not None else 'none', ITEMS[dE][0] if dE is not None else 'none')
        if ok else 'MISS — all 20 items stand'))
elif mode == '--p6':
    # ROUND 3 P6 (prereg @1967b15): coverage-GATED EB shrinkage — only n_chunks<8 members shrink.
    ctl, (names, Z, raw, gsc, half, nch) = metrics(ct_equal(), 'control (frozen snapshot)')
    ct = ct_equal()
    gm2 = collections.defaultdict(list)
    for (m, g, ci), row in zip(meta, ct): gm2[m].append(row)
    ks = {}
    for ti, t in enumerate(TR):
        w_list, means, ns = [], [], []
        for m, rws in gm2.items():
            a = np.array([r[ti] for r in rws])
            if len(a) >= 2: w_list.append(a.var(ddof=1))
            means.append(a.mean()); ns.append(len(a))
        s2w = float(np.mean(w_list)); nbar = float(np.mean(ns))
        s2b = max(1e-9, float(np.var(means, ddof=1)) - s2w / nbar)
        ks[t] = s2w / s2b
    print('  k per trait: ' + ' '.join('%s %.1f' % (t, ks[t]) for t in TR))
    nch_h = {par: collections.Counter() for par in (0, 1)}
    for (m, g, ci) in meta: nch_h[ci % 2][m] += 1
    def gated_half(halfdict, par):
        out = {}
        for m, v in halfdict.items():
            if nch.get(m, 0) >= 8: out[m] = v            # well-measured: untouched BY CONSTRUCTION
            else:
                n = max(1, nch_h[par][m])
                out[m] = np.array([v[ti] * (n / (n + ks[t])) for ti, t in enumerate(TR)])
        return out
    sh = {par: gated_half(half[par], par) for par in (0, 1)}
    both = [m for m in names if m in sh[0] and m in sh[1]]
    sb = {}
    for ti, t in enumerate(TR):
        r = pearson([sh[0][m][ti] for m in both], [sh[1][m][ti] for m in both])
        sb[t] = 2 * r / (1 + r) if r > -1 else -1
    sb_mean = sum(sb.values()) / 5
    moved = sum(1 for m in names if nch.get(m, 0) >= 8)  # structural: gated members never rescored
    print('  P6 gated split-half SB: ' + ' '.join('%s %.3f' % (t, sb[t]) for t in TR) + ' | mean %.3f' % sb_mean)
    n_thin = sum(1 for m in names if nch.get(m, 0) < 8)
    print('  gated members (n<8): %d of %d' % (n_thin, len(names)))
    bars = [('SB mean >= ctrl+0.015', sb_mean >= ctl['sb_mean'] + 0.015, '%.3f vs %.3f' % (sb_mean, ctl['sb_mean'])),
            ('n>=8 members moved == 0', True, 'structural (gate excludes them)')]
    ok = all(b[1] for b in bars)
    for nm, hit, det in bars: print('  BAR %-30s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', det))
    print('P6 VERDICT: %s' % ('PASS — promote gated shrinkage for n<8 members' if ok else 'MISS — raw stands'))
elif mode == '--p6diag':
    # HONESTY DIAGNOSTIC (audit 2026-07-07): decompose P6's SB gain into REAL vs shrinkage artifact.
    # Shrinking BOTH halves toward 0 mechanically inflates their correlation. The clean test: shrink
    # ONE half only (cross). If cross-SB ~= raw-raw SB, the double-shrink gain was artifact; if
    # cross-SB is elevated, some gain is real regularization.
    names, Z, raw, gsc, half, nch = aggregate(ct_equal())
    ct = ct_equal(); gm2 = collections.defaultdict(list)
    for (m, g, ci), row in zip(meta, ct): gm2[m].append(row)
    ks = {}
    for ti, t in enumerate(TR):
        wl, mn, ns = [], [], []
        for m, r in gm2.items():
            a = np.array([x[ti] for x in r])
            if len(a) >= 2: wl.append(a.var(ddof=1))
            mn.append(a.mean()); ns.append(len(a))
        s2w = float(np.mean(wl)); nbar = float(np.mean(ns))
        s2b = max(1e-9, float(np.var(mn, ddof=1)) - s2w / nbar); ks[t] = s2w / s2b
    nch_h = {p: collections.Counter() for p in (0, 1)}
    for (m, g, ci) in meta: nch_h[ci % 2][m] += 1
    def sh(v, m, par):
        n = max(1, nch_h[par][m])
        return np.array([v[ti] * (n / (n + ks[t])) if nch.get(m, 0) < 8 else v[ti] for ti, t in enumerate(TR)])
    both = [m for m in names if m in half[0] and m in half[1]]
    def sbmean(A, Bd):
        out = []
        for ti, t in enumerate(TR):
            r = pearson([A[m][ti] for m in both], [Bd[m][ti] for m in both])
            out.append(2 * r / (1 + r) if r > -1 else -1)
        return sum(out) / 5
    rawraw = sbmean(half[0], half[1])
    shsh = sbmean({m: sh(half[0][m], m, 0) for m in both}, {m: sh(half[1][m], m, 1) for m in both})
    crossA = sbmean({m: sh(half[0][m], m, 0) for m in both}, half[1])
    print('P6 SB decomposition (mean over 5 traits):')
    print('  raw-raw (control, shipped comparison) : %.3f' % rawraw)
    print('  shrunk-shrunk (the P6 headline number): %.3f' % shsh)
    print('  shrunk-RAW cross (one side only)      : %.3f' % crossA)
    print('  => artifact share (shsh-cross) = %.3f | real-ish (cross-rawraw) = %.3f'
          % (shsh - crossA, crossA - rawraw))
elif mode == '--p7':
    # ROUND 3 P7 (prereg @cfc9119): within-chunk ipsatization — subtract each chunk's mean keyed-
    # item score from all 20 items (chunk TONE removed; trait PROFILE preserved), then score.
    Xi = X - X.mean(axis=1, keepdims=True)
    ct7 = np.stack([Xi[:, TRAIT_COLS[t]].mean(axis=1) for t in TR], axis=1)
    p7, _ = metrics(ct7, 'P7: within-chunk ipsatized items')
    bars = [('A-ES |r| <= 0.50 (RKH .443)', p7['a_es'] <= 0.50, '%.3f' % p7['a_es']),
            ('discriminant <= 0.30', p7['disc_mean'] <= 0.30, '%.3f' % p7['disc_mean']),
            ('MTMM >= 0.450', p7['mtmm_mean'] >= 0.450, '%.3f' % p7['mtmm_mean']),
            ('SB mean >= 0.72', p7['sb_mean'] >= 0.72, '%.3f' % p7['sb_mean']),
            ('known-groups >= 4/5', p7['kg'] >= 4, '%d/5' % p7['kg']),
            ('C votes >= +0.126', (p7['c_votes'][0] or -9) >= 0.126, '%+.3f' % (p7['c_votes'][0] or -9)),
            ('C followthrough >= +0.081', (p7['c_ft'][0] or -9) >= 0.081, '%+.3f' % (p7['c_ft'][0] or -9))]
    ok = all(b[1] for b in bars)
    for nm, hit, det in bars: print('  BAR %-30s %s (%s)' % (nm, 'CLEAR' if hit else 'MISS', det))
    print('P7 VERDICT: %s' % ('PASS — promote ipsatized scoring (composes with P6 gating)'
                              if ok else 'MISS — scoring layer EXHAUSTED; A-ES documented vs RKH benchmark; next lever = itemset v3 (funding decision)'))
else:
    print('unknown mode', mode)
