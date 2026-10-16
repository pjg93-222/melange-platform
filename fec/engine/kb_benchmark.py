import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""KATZ-BONACICH TARGETING BENCHMARK — Battaglini & Patacchini (NBER w22739, 'Influencing
Connected Legislators'): in their equilibrium, interest groups allocate contributions across
socially-connected legislators IN PROPORTION TO KATZ-BONACICH CENTRALITY (b = (I - dA)^-1 * 1)
— network position, not just pivotality, prices the member.

Diagnostic (run-4k; nothing in the chain consumes it): compute KB centrality on OUR diffusion
kernel P = build_P(adj) (the model's actual influence physics) and report Spearman rank
correlations against
  (1) the G-K optimizer's suggested allocation (optimize_result.json),
  (2) observed CU money (congestion 'cu'),
  (3) observed industry money (banks+finserv PACs),
  (4) the legislative score.
Reading: B-P predict money loads on centrality. Our allocator ALSO prices stance, receptivity,
electoral leverage and money-response capacity, so rho(alloc,KB) well below 1 is expected — the
interesting quantities are the GAP between (1) and (2)/(3) (is anyone playing the B-P equilibrium
better than anyone else?) and members with top-decile KB but $0 suggested (centrality the model
deliberately declines to buy). Deterministic; pure linear algebra."""
import json, sys, os
import moneyball_core as MBC
from moneyball_core import dump_atomic
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
DELTA_FRAC = float(os.environ.get('KB_DELTA', '0.85'))   # delta = frac / spectral_radius (must be <1/lambda_max to converge)

C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
members = list(C)
adj = MBC.load_network()
P = MBC.build_P(adj)                     # receiver-susceptibility-aware activation probs (the live kernel)
idx = {n: i for i, n in enumerate(members)}; N = len(members)
rows = {i: [(idx[v], p) for v, p in P[u].items() if v in idx] for u, i in ((u, idx[u]) for u in members if u in P)}

def matvec(x):
    # (Ax)_i = sum_j P[i][j] x_j  -> b_i counts weighted OUT-walks from i (downstream influence,
    # the B-P notion: a member is valuable for who they can move, not who moves them)
    y = [0.0] * N
    for i, nb in rows.items():
        acc = 0.0
        for j, p in nb: acc += p * x[j]
        y[i] = acc
    return y

# spectral radius via power iteration (deterministic start)
x = [1.0] * N; lam = 1.0
for _ in range(120):
    y = matvec(x); nrm = max(abs(v) for v in y) or 1.0
    lam = nrm; x = [v / nrm for v in y]
delta = DELTA_FRAC / lam if lam > 0 else 0.5
print('kernel spectral radius ~%.3f -> delta=%.4f (KB series converges)' % (lam, delta))
# KB centrality b = sum_{k>=1} (delta*A')^k 1  (receiving-side: who is reachable/influential downstream)
b = [0.0] * N; term = [1.0] * N
for _ in range(400):
    term = matvec(term); term = [delta * v for v in term]
    mx = 0.0
    for i in range(N):
        b[i] += term[i]
        if abs(term[i]) > mx: mx = abs(term[i])
    if mx < 1e-12: break
KB = {members[i]: b[i] for i in range(N)}

opt = json.load(open(B + r'\optimize_result.json')); alloc = opt.get('alloc', {})
score = MBC.scores()
cu = {n: (C[n].get('cu') or 0) for n in members}
ind = {n: (C[n].get('banks') or 0) + (C[n].get('finserv') or 0) for n in members}

def spearman(d1, d2, keys):
    ks = [k for k in keys if k in d1 and k in d2]
    if len(ks) < 5: return None, 0
    def ranks(d):
        sv = sorted(ks, key=lambda k: d[k]); r = {}
        i = 0
        while i < len(sv):
            j = i
            while j + 1 < len(sv) and d[sv[j + 1]] == d[sv[i]]: j += 1
            for t in range(i, j + 1): r[sv[t]] = (i + j) / 2.0
            i = j + 1
        return r
    r1, r2 = ranks(d1), ranks(d2)
    n = len(ks); m = (n - 1) / 2.0
    num = sum((r1[k] - m) * (r2[k] - m) for k in ks)
    den = (sum((r1[k] - m) ** 2 for k in ks) * sum((r2[k] - m) ** 2 for k in ks)) ** 0.5 or 1.0
    return num / den, n

pairs = [('optimizer alloc $ (all members, $0 for unallocated)', {n: alloc.get(n, 0) for n in members}, members),
         ('optimizer alloc $ (allocated members only)', alloc, list(alloc)),
         ('observed CU money', cu, members),
         ('observed industry money (banks+finserv)', ind, members),
         ('legislative score', score, members)]
res = {}
print('\nBattaglini-Patacchini check — Spearman rho of Katz-Bonacich centrality vs:')
for name, d, keys in pairs:
    rho, n = spearman(KB, d, keys)
    res[name] = {'rho': round(rho, 4) if rho is not None else None, 'n': n}
    print('  %-52s %s (n=%d)' % (name, 'rho=%+.3f' % rho if rho is not None else 'rho=n/a', n))
top = sorted(members, key=lambda n: -KB[n])[:max(10, N // 10)]
unbought = [n for n in top if alloc.get(n, 0) == 0][:15]
print('\ntop-decile KB centrality with $0 suggested (centrality the model declines to buy — stance/receptivity/capacity price it out):')
print('  ' + (', '.join(unbought) or '(none)'))
dump_atomic({'delta': round(delta, 5), 'spectral_radius': round(lam, 4),
    'kb': {n: round(KB[n], 5) for n in members}, 'correlations': res,
    'top_decile_unallocated': unbought,
    'provenance': 'Battaglini-Patacchini w22739 equilibrium benchmark: KB centrality on build_P kernel vs allocations/money/score; diagnostic layer, chain does NOT consume'},
    B + r'\data\network\kb_benchmark.json')
print('wrote data/network/kb_benchmark.json (diagnostic; not consumed by the chain)')
