# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""BIG5 VALIDATION HARNESS (2026-07-04 Big5 campaign, user: 'same highly methodologically driven
approach'). Method-agnostic: scores ANY candidate trait artifact {member:{O,C,E,A,ES,...}} on the
four validity axes the no-self-report literature supports:
  1. CONVERGENT  — Pearson r vs RKH published congressional estimates (replication data, 146-member
     roster overlap via bioguide->icpsr). RKH measured 1996-2014 floor speech: correlations are a
     persona-transfer anchor, not ground truth; direction+magnitude vs the lexical baseline is what
     matters.
  2. DISCRIMINANT — inter-trait |r| matrix. Everything-correlates = halo/method variance
     (the known LLM failure mode). Flag mean off-diagonal |r| > 0.5.
  3. KNOWN-GROUPS — party point-biserial per trait vs RKH Table-1 replicated signs
     (conservatism: O -, C +, E -, A -, ES +; party R=conservative proxy).
  4. RELIABILITY — split-half: pass a second artifact scored on the OTHER half of each member's
     corpus (--half2 path); Spearman-Brown corrected r per trait. Skipped if not provided.
Usage:
  python fec/members/validate_big5.py <candidate.json> [--traits-key traits] [--half2 <other-half.json>]
  (candidate may be big5_traits.json itself: --traits-key traits)"""
import csv, json, os, sys, math
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
RKH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'liwc', 'rkh')
TR = ['O', 'C', 'E', 'A', 'ES']
MAPS = {'O': 'openn', 'C': 'consc', 'E': 'extra', 'A': 'agree', 'ES': 'emoti'}
EXPECT_R_SIGN = {'O': -1, 'C': +1, 'E': -1, 'A': -1, 'ES': +1}   # trait -> sign of (R - D) difference per RKH conservatism findings

def pearson(a, b):
    n = len(a)
    if n < 3: return 0.0
    ma = sum(a) / n; mb = sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a)); sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if sa == 0 or sb == 0: return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)

path = sys.argv[1] if len(sys.argv) > 1 else B + r'\data\members\big5_traits.json'
tkey = sys.argv[sys.argv.index('--traits-key') + 1] if '--traits-key' in sys.argv else None
cand = json.load(open(path, encoding='utf-8'))
if tkey: cand = cand[tkey]
elif 'traits' in cand and isinstance(cand['traits'], dict): cand = cand['traits']
cand = {m: d for m, d in cand.items() if isinstance(d, dict) and all(t in d for t in TR)}
print('candidate: %s | %d members' % (os.path.basename(path), len(cand)))

C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}

# ---- 1. convergent vs RKH ----
rkh = {}
with open(os.path.join(RKH, 'congressbios.tab'), encoding='utf-8', errors='replace') as fh:
    for r in csv.DictReader(fh, delimiter='\t'):
        try:
            i = int(float(r['icpsr']))
            rkh[i] = {k: float(r[k + '_avg']) for k in MAPS.values()}
        except (ValueError, KeyError, TypeError): continue
b2i = {}; b2p = {}   # bioguide -> icpsr, party (voteview party_code 200=R, 100=D)
for cn in (119, 118, 117):
    p = os.path.join(B, 'votes', 'HS%d_members.csv' % cn)
    if not os.path.exists(p): continue
    for r in csv.DictReader(open(p, encoding='utf-8')):
        bg = (r.get('bioguide_id') or '').strip()
        pc = (r.get('party_code') or '').strip()
        if bg and bg not in b2p and pc in ('100', '200'): b2p[bg] = 'R' if pc == '200' else 'D'
        try: ic = int(float(r.get('icpsr') or ''))
        except ValueError: continue
        if bg and bg not in b2i: b2i[bg] = ic
overlap = [m for m in cand if b2i.get((C.get(m) or {}).get('bioguide') or '') in rkh]
print('\n[1] CONVERGENT vs RKH (n=%d overlap):' % len(overlap))
for t in TR:
    xs = [cand[m][t] for m in overlap]
    ys = [rkh[b2i[C[m]['bioguide']]][MAPS[t]] for m in overlap]
    print('    %-3s r = %+0.3f' % (t, pearson(xs, ys)))

# ---- 2. discriminant ----
ms = list(cand)
print('\n[2] DISCRIMINANT inter-trait |r| (halo check):')
offs = []
for i, a in enumerate(TR):
    row = []
    for b in TR:
        r = pearson([cand[m][a] for m in ms], [cand[m][b] for m in ms])
        row.append(r)
        if a < b: offs.append(abs(r))
    print('    %-3s %s' % (a, ' '.join('%+0.2f' % v for v in row)))
mo = sum(offs) / len(offs)
print('    mean off-diagonal |r| = %.2f %s' % (mo, '(HALO WARNING >0.50)' if mo > 0.5 else 'OK'))

# ---- 3. known-groups: party ----
print('\n[3] KNOWN-GROUPS party (R minus D mean, RKH-replicated expected sign):')
for t in TR:
    rs = [cand[m][t] for m in ms if b2p.get((C.get(m) or {}).get('bioguide') or '') == 'R']
    ds = [cand[m][t] for m in ms if b2p.get((C.get(m) or {}).get('bioguide') or '') == 'D']
    if not rs or not ds: continue
    diff = sum(rs) / len(rs) - sum(ds) / len(ds)
    ok = (diff > 0) == (EXPECT_R_SIGN[t] > 0)
    print('    %-3s R-D = %+0.3f expected %s  %s' % (t, diff, '+' if EXPECT_R_SIGN[t] > 0 else '-',
          'consistent' if ok else 'INCONSISTENT'))

# ---- 3b. MTMM cross-genre (artifacts with a genre_scores matrix, e.g. big5_llm_*.json):
# same-trait cross-genre r must exceed cross-trait within-genre mean (Park's discriminant logic
# extended across genres; Birkenmaier multi-source design) ----
full = json.load(open(path, encoding='utf-8'))
gsc = full.get('genre_scores') or {}
if gsc:
    genres = sorted({g for d in gsc.values() for g in d})
    if len(genres) >= 2:
        g1, g2 = genres[0], genres[1]
        both = [m for m in gsc if g1 in gsc[m] and g2 in gsc[m]]
        if len(both) >= 20:
            print('\n[3b] MTMM cross-genre (%s vs %s, n=%d):' % (g1, g2, len(both)))
            mono = []
            for t in TR:
                r = pearson([gsc[m][g1][t] for m in both], [gsc[m][g2][t] for m in both])
                mono.append(r)
                print('    %-3s same-trait cross-genre r = %+0.3f' % (t, r))
            het = []
            for g in (g1, g2):
                for i, a in enumerate(TR):
                    for b_ in TR[i + 1:]:
                        het.append(abs(pearson([gsc[m][g][a] for m in both], [gsc[m][g][b_] for m in both])))
            print('    mean same-trait cross-genre = %+0.3f vs mean cross-trait within-genre = %0.3f  %s' % (
                sum(mono) / len(mono), sum(het) / len(het),
                'OK (validity > halo)' if sum(mono) / len(mono) > sum(het) / len(het) else 'FAIL — genre/halo variance dominates'))

# ---- 4. split-half reliability ----
if '--half2' in sys.argv:
    p2 = sys.argv[sys.argv.index('--half2') + 1]
    c2 = json.load(open(p2, encoding='utf-8'))
    if 'traits' in c2: c2 = c2['traits']
    both = [m for m in cand if m in c2 and all(t in c2[m] for t in TR)]
    print('\n[4] SPLIT-HALF reliability (n=%d, Spearman-Brown corrected):' % len(both))
    for t in TR:
        r = pearson([cand[m][t] for m in both], [c2[m][t] for m in both])
        sb = 2 * r / (1 + r) if r > -1 else 0.0
        print('    %-3s half-r = %+0.3f -> SB = %+0.3f %s' % (t, r, sb, '(unstable <0.5)' if sb < 0.5 else ''))
else:
    print('\n[4] split-half: skipped (pass --half2 <artifact scored on other corpus half>)')
