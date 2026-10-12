import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""HIDDEN-COALITION DETECTION ON THE SIGNED MENTION LAYER — Aref & Neal (Sci Reports 2021),
"Identifying hidden coalitions in the US House by optimally partitioning signed networks based on
generalized balance". User's bib (run-4k): "presents an alternative layer that can be substituted or
added into future network models"; their headline = a THIRD House coalition (unstable-median,
Fitzpatrick-class) invisible to party labels.

Ours: the signed graph is member_references.json (cross-media member->member mentions, Fable-valenced;
run-4b/4f layer). Edge sign = sign(net); weight = |net|. Aref-Neal partition k clusters minimizing
FRUSTRATION (intra-cluster negative + inter-cluster positive edge weight; generalized balance). They
solve exactly with ILP; at our size (hundreds of nodes, ~3.7k edges) a deterministic multi-restart
local search gets a tight upper bound — this is a DIAGNOSTIC layer (nothing in the chain consumes it):
  F(k) upper bound, k=1..3; party-recovery check (their House result: 2-partition ~= party);
  cross-party assignments = hidden-coalition candidates; k=3 third-cluster roster.
Writes signed_coalitions.json (new, non-chain artifact). Deterministic (seeded restarts)."""
import json, sys, os, random, collections
import moneyball_core as MBC
from moneyball_core import dump_atomic
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
MINW = float(os.environ.get('SC_MINW', '0.0'))   # optional |net| floor for edge inclusion
RESTARTS = int(os.environ.get('SC_RESTARTS', '60'))

mr = json.load(open(B + r'\member_references.json'))
C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
party = MBC.party()   # kernel party map (congestion rows don't carry a party field)
E = []
for e in mr['edges']:
    s = 1 if e['net'] > 0 else (-1 if e['net'] < 0 else 0)
    if s == 0 or abs(e['net']) < MINW: continue          # ambivalent/neutral dyads carry no sign
    E.append((e['a'], e['b'], s, abs(e['net'])))
nodes = sorted({a for a, b, s, w in E} | {b for a, b, s, w in E})
idx = {n: i for i, n in enumerate(nodes)}
adj = collections.defaultdict(list)
for a, b, s, w in E:
    adj[idx[a]].append((idx[b], s, w)); adj[idx[b]].append((idx[a], s, w))
TOTW = sum(w for _, _, _, w in E)
npos = sum(1 for e in E if e[2] > 0); nneg = len(E) - npos
print('signed mention layer: %d members, %d signed edges (%d+ / %d-), total weight %.1f' % (len(nodes), len(E), npos, nneg, TOTW))

def frustration(lab):
    f = 0.0
    for a, b, s, w in E:
        same = lab[idx[a]] == lab[idx[b]]
        if (s < 0 and same) or (s > 0 and not same): f += w
    return f

def local_search(lab, K):
    # single-node best-move descent (delta computed from incident edges only)
    improved = True
    while improved:
        improved = False
        for i in range(len(nodes)):
            base = sum(w * (1 if ((s < 0 and lab[i] == lab[j]) or (s > 0 and lab[i] != lab[j])) else 0) for j, s, w in adj[i])
            best, bestc = 0.0, lab[i]
            for c in range(K):
                if c == lab[i]: continue
                f = sum(w * (1 if ((s < 0 and c == lab[j]) or (s > 0 and c != lab[j])) else 0) for j, s, w in adj[i])
                if f < base - 1e-12 and (base - f) > best: best, bestc = base - f, c
            if bestc != lab[i]: lab[i] = bestc; improved = True
    return lab

def solve(K, seeds):
    best, bl = None, None
    for si, init in enumerate(seeds):
        lab = local_search(list(init), K)
        f = frustration(lab)
        if best is None or f < best: best, bl = f, lab
    return best, bl

# k=1 baseline: all negatives frustrated
f1 = sum(w for _, _, s, w in E if s < 0)
# seeds: party split (D/I=0, R=1) + deterministic random restarts
pseed = [0 if party.get(n, '?') in ('D', 'I', 'ID', 'L') else 1 for n in nodes]
seeds2 = [pseed] + [[random.Random(1009 * r).randint(0, 1) for _ in nodes] for r in range(RESTARTS)]
f2, l2 = solve(2, seeds2)
seeds3 = [[x for x in l2]] + [[random.Random(2003 * r).randint(0, 2) for _ in nodes] for r in range(RESTARTS)]
# let the k=3 party-informed seed start from the best k=2 answer (third cluster must EARN members)
f3, l3 = solve(3, seeds3)
print('frustration (weighted, lower=more balanced): F(1)=%.1f (%.1f%%)  F(2)<=%.1f (%.1f%%)  F(3)<=%.1f (%.1f%%)'
      % (f1, 100 * f1 / TOTW, f2, 100 * f2 / TOTW, f3, 100 * f3 / TOTW))

def comp(lab, K):
    out = []
    for c in range(K):
        mem = [nodes[i] for i in range(len(nodes)) if lab[i] == c]
        pc = collections.Counter(party.get(m, '?') for m in mem)
        out.append((c, mem, dict(pc)))
    return out

c2 = comp(l2, 2)
print('\nk=2 partition vs party (Aref-Neal House check; NOTE: our substrate is the MENTION layer,')
print('%.0f%% positive weight, so near-single-bloc balance is expected — the splinter IS the finding):' % (100 * sum(w for _, _, s, w in E if s > 0) / TOTW))
bipolar = all(len(mem) >= 0.10 * len(nodes) for _, mem, _ in c2)
xparty, agree = [], None
for c, mem, pc in c2:
    print('  cluster %d: %d members, party mix %s' % (c, len(mem), pc))
    if 0 < len(mem) <= 30: print('    roster: ' + ', '.join(sorted(mem)))
    if bipolar:
        dom = 'D' if pc.get('D', 0) >= pc.get('R', 0) else 'R'
        xparty += [m for m in mem if party.get(m) in ('D', 'R') and party.get(m) != dom]
if bipolar:
    agree = 1 - len(xparty) / max(1, sum(1 for n in nodes if party.get(n) in ('D', 'R')))
    print('  party agreement %.1f%%; CROSS-PARTY (hidden-coalition candidates): %s' % (100 * agree, ', '.join(sorted(xparty)) or '(none)'))
else:
    print('  partition is single-bloc + SPLINTER (not bipolar) -> Aref-Neal party-recovery check not applicable here;')
    print('  the splinter roster above = the antagonist set (members whose signed mention ties frustrate the main bloc).')
c3 = comp(l3, 3)
third = min(c3, key=lambda t: len(t[1]))
print('\nk=3 third cluster (%d members; their headline: unstable-median but effective bloc): %s' % (len(third[1]), ', '.join(sorted(third[1])[:25]) + (' …' if len(third[1]) > 25 else '')))
dump_atomic({'nodes': len(nodes), 'edges': len(E), 'pos': npos, 'neg': nneg, 'total_weight': round(TOTW, 2),
    'frustration': {'k1': round(f1, 2), 'k2': round(f2, 2), 'k3': round(f3, 2), 'k2_pct': round(100 * f2 / TOTW, 2)},
    'party_agreement_k2': (round(agree, 4) if agree is not None else None), 'cross_party_k2': sorted(xparty),
    'splinter_k2': sorted(min(([nodes[i] for i in range(len(nodes)) if l2[i] == c] for c in (0, 1)), key=len)),
    'partition_k2': {nodes[i]: l2[i] for i in range(len(nodes))},
    'partition_k3': {nodes[i]: l3[i] for i in range(len(nodes))},
    'provenance': 'Aref-Neal 2021 generalized-balance frustration minimization (multi-restart local search, deterministic seeds); signed graph = member_references.json net-valence; diagnostic layer, chain does NOT consume'},
    B + r'\data\lobbying\signed_coalitions.json')
print('\nwrote signed_coalitions.json (diagnostic; not consumed by the chain)')
