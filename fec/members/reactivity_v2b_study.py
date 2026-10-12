# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""REACTIVITY CRITERION STUDY, PART 2 — variant selection under a PRE-STATED rule (user delegated
'Whatever is most optimal', 2026-07-10; the S3-06 delegation pattern).

Part 1 (reactivity_v2_study.py) showed the trade: V0 per-show = SB .508 but MEM_N 216; V1
per-network = MEM_N 271 but SB .301 (noise dilution from blending show cultures). A noisier
criterion attenuates any true text signal by sqrt(rel ratio) ~= 0.77 — crossing the n-gate while
shrinking the detectable effect is the LETTER of the gate against its PURPOSE. So: test variants
that recover fragmented turns without paying full dilution.

SELECTION RULE — STATED HERE, BEFORE ANY VARIANT RESULT IS COMPUTED (anti-shopping):
  Among variants with MEM_N >= 250:
    require (i) appearance-split-half SB >= .45 (<= ~6% attenuation vs V0's .508),
            (ii) |gender| and |party| loadings <= V0's (|-.097|, |.087|) — no fairness regression,
            (iii) agreement with V0 >= .85 on double-scored members (same construct).
  Winner = highest appearance-SB among qualifiers. No qualifier => HOLD V0, keep collecting
  (the honest 'not yet'); V1 is NOT adopted as a fallback. The 5-seed synthetic diagnostic on the
  winner's actual stratum remains the FINAL power arbiter before any prereg lock — a criterion
  too noisy to support recovery will refuse itself there.

VARIANTS (identical machinery, only grouping/floors differ):
  A  per-show, floors 12/5    (thin shows admitted to z)
  B  per-show, floors 8/8     (thin member-cells admitted)
  C  per-show 12/8 + NETWORK FALLBACK POOL: turns in cells failing the member-cell floor, or in
     shows failing the members floor, route to a '<network>#pool' cell per member (floors 12/8 on
     pool cells too). Recovers ONLY the orphaned turns; qualified shows keep per-show precision.
  D  per-show, floors 10/6    (mild joint relaxation)
Criterion-side only; no trait model, no look; output data/members/reactivity_v2b_study.json, wired nowhere."""
import sqlite3, collections, json, re, hashlib
import numpy as np
_s.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT

def show_of(pkg):
    p = (pkg or "").lower()
    if "transcripts.cnn.com" in p:
        m = re.search(r"/show/(\w+)/", p)
        return "cnn:" + (m.group(1) if m else "?")
    if "nbcnews" in p: return "mtp"
    if "pbs.org" in p: return "pbs"
    if "npr.org" in p: return "npr"
    return "other"

def network_of(pkg):
    p = (pkg or "").lower()
    if "transcripts.cnn.com" in p: return "cnn"
    if "nbcnews" in p: return "mtp"
    if "pbs.org" in p: return "pbs"
    if "npr.org" in p: return "npr"
    if "cbsnews" in p: return "cbs"
    if "abcnews" in p or "abc.com" in p: return "abc"
    if "foxnews" in p: return "fox"
    if "hughhewitt" in p or "hewitt" in p: return "hewitt"
    if "ms.now" in p: return "msnow"
    return "other"

def cutoff(t):
    t = t.strip()
    return t.endswith("--") or t.endswith("—") or t.endswith("…") or t.endswith("...") or (t and t[-1] not in ".!?\"')”")

def rr(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if len(a) < 3 or a.std() == 0 or b.std() == 0: return float('nan')
    return float(np.corrcoef(a, b)[0, 1])

def sb2(r): return 2 * r / (1 + r) if r == r else float('nan')

con = sqlite3.connect(B + r'\social_media.db', timeout=60)
con.execute('PRAGMA query_only=1')
app = collections.defaultdict(list)
for m, pkg, t in con.execute("select member, package, text from speeches where source='INTERVIEW' order by rowid"):
    if t and t.strip(): app[(m, pkg)].append(cutoff(t))
print('INTERVIEW turns: %d | appearances: %d' % (sum(len(v) for v in app.values()), len(app)))

def cells_plain(sub, min_turns, min_members):
    cell = collections.defaultdict(lambda: [0, 0])
    for (m, pkg), flags in sub.items():
        g = show_of(pkg); c = cell[(m, g)]
        c[0] += sum(flags); c[1] += len(flags)
    rows = collections.defaultdict(dict)
    for (m, g), (cu, n) in cell.items():
        if n >= min_turns: rows[g][m] = (cu / n, n)
    return {g: mem for g, mem in rows.items() if len(mem) >= min_members}

def cells_fallback(sub, min_turns=12, min_members=8):
    """Variant C: per-show cells that clear both floors keep per-show precision; every turn NOT in
    such a cell (thin member-cell or thin show) reroutes to the member's '<network>#pool' cell."""
    cell = collections.defaultdict(lambda: [0, 0])
    for (m, pkg), flags in sub.items():
        g = show_of(pkg); c = cell[(m, g)]
        c[0] += sum(flags); c[1] += len(flags)
    rows = collections.defaultdict(dict)
    for (m, g), (cu, n) in cell.items():
        if n >= min_turns: rows[g][m] = (cu / n, n)
    ok = {g for g, mem in rows.items() if len(mem) >= min_members}
    # rebuild: qualified turns stay; the rest pool per network
    cell2 = collections.defaultdict(lambda: [0, 0])
    for (m, pkg), flags in sub.items():
        g = show_of(pkg)
        qualified = g in ok and cell[(m, g)][1] >= min_turns
        key = (m, g) if qualified else (m, network_of(pkg) + '#pool')
        c = cell2[key]; c[0] += sum(flags); c[1] += len(flags)
    rows2 = collections.defaultdict(dict)
    for (m, g), (cu, n) in cell2.items():
        if n >= min_turns: rows2[g][m] = (cu / n, n)
    return {g: mem for g, mem in rows2.items() if len(mem) >= min_members}

def score_from(rows):
    zsum = collections.defaultdict(float); zwt = collections.defaultdict(float); qual = collections.Counter()
    for g, mem in rows.items():
        vals = np.array([r for r, _ in mem.values()])
        mu, sd = vals.mean(), vals.std()
        if sd == 0: continue
        for m, (r, n) in mem.items():
            zsum[m] += ((r - mu) / sd) * n; zwt[m] += n; qual[m] += n
    return {m: zsum[m] / zwt[m] for m in zsum if zwt[m] > 0}, qual

def evaluate(cellfn):
    rows = cellfn(app)
    score, qual = score_from(rows)
    halves = {0: {}, 1: {}}
    for (m, pkg), flags in app.items():
        halves[int(hashlib.md5(pkg.encode()).hexdigest(), 16) % 2][(m, pkg)] = flags
    hs = []
    for h in (0, 1):
        s, q = score_from(cellfn(halves[h]))
        hs.append((s, q))
    (s0, q0), (s1, q1) = hs
    both = [m for m in s0 if m in s1 and q0[m] >= 15 and q1[m] >= 15]
    r = rr([s0[m] for m in both], [s1[m] for m in both])
    return score, qual, r, sb2(r), len(both)

import moneyball_core as MC
P = MC.party()
b2n = MC.bio2name(); gmap = {}
import csv as _csv
with open(B + r'\fec\legislators.csv', encoding='utf-8') as f:
    for row in _csv.DictReader(f):
        m = b2n.get(row.get('bioguide_id'))
        if m: gmap[m] = row.get('gender', '')
BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
VV = json.load(open(B + r'\data\members\voteview_consistency.json', encoding='utf-8'))['members']
BEH = ('breadth_z', 'diligence_z', 'visibility_z', 'cooperation_z', 'dominance_z')
MEM = {m for m in BD if m in VV and P.get(m) in ('D', 'R') and all((BD[m] or {}).get(v) is not None for v in BEH)}

VARIANTS = [
    ('V0 12/8 (shipped)', lambda sub: cells_plain(sub, 12, 8)),
    ('A  12/5', lambda sub: cells_plain(sub, 12, 5)),
    ('B   8/8', lambda sub: cells_plain(sub, 8, 8)),
    ('C  12/8+netpool', cells_fallback),
    ('D  10/6', lambda sub: cells_plain(sub, 10, 6)),
]
base_score = None
rep = {}
for name, fn in VARIANTS:
    score, qual, r, sb, n = evaluate(fn)
    cov = {m for m, t in qual.items() if t >= 30}
    memn = len(cov & MEM)
    dm = [m for m in score if gmap.get(m) in ('M', 'F')]
    gf = rr([score[m] for m in dm], [1.0 if gmap[m] == 'F' else 0.0 for m in dm])
    dp = [m for m in score if P.get(m) in ('D', 'R')]
    pr = rr([score[m] for m in dp], [1.0 if P.get(m) == 'R' else 0.0 for m in dp])
    if base_score is None: base_score = score; agree = 1.0
    else:
        bm = [m for m in base_score if m in score]
        agree = rr([base_score[m] for m in bm], [score[m] for m in bm])
    rep[name] = dict(MEM_N=memn, covered=len(cov), app_sb=sb, app_r=r, app_n=n,
                     gender=gf, party=pr, agree_v0=agree, scored=len(score))
    print('%-18s MEM_N=%3d cov=%3d | app-SB=%.3f (r=%.3f n=%d) | g=%+.3f p=%+.3f | agreeV0=%.3f | scored=%d'
          % (name, memn, len(cov), sb, r, n, gf, pr, agree, len(score)))

print()
print('SELECTION RULE (pre-stated): MEM_N>=250 AND SB>=.45 AND |g|<=.097 AND |p|<=.087 AND agree>=.85; winner = max SB; none => HOLD V0')
qualifiers = {k: v for k, v in rep.items() if not k.startswith('V0')
              and v['MEM_N'] >= 250 and v['app_sb'] >= .45
              and abs(v['gender']) <= .097 and abs(v['party']) <= .087 and v['agree_v0'] >= .85}
if qualifiers:
    w = max(qualifiers, key=lambda k: qualifiers[k]['app_sb'])
    print('WINNER: %s' % w)
else:
    print('NO QUALIFIER -> HOLD V0, keep collecting (V1 explicitly not adopted as fallback)')
with open(B + r'\data\members\reactivity_v2b_study.json', 'w', encoding='utf-8') as f:
    json.dump(rep, f, indent=1, default=str)
print('wrote data/members/reactivity_v2b_study.json (record only)')
