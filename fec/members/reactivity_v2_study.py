# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""REACTIVITY CRITERION-SIDE STUDY (Tier B, user-directed 2026-07-10 "Start tier b") — the coop_v2
playbook: measurement comparison FIRST, ratification after, spec change only if the evidence
carries it. NO trait model, NO text features, NO look — criterion side only.

Question: the shipped reactivity spec computes cutoff proneness with per-PROGRAM z where CNN
splits per SHOW (program_of): members' turns fragment below the >=12/program floor and are
DISCARDED (Deluzio 43 raw -> 12 qualifying). Variant V1 pools per NETWORK (cnn as one program,
etc.). If V1 measures with equal-or-better reliability and no worse demographic loadings, the
fragmentation loss is measurement waste; if V0's show-level control is load-bearing (shows have
distinct interruption cultures), V1's reliability will pay for it and this dies honestly.

Metrics (both variants, identical machinery):
  coverage        members with >=30 qualifying turns (and the MEM_N impact)
  reliability     appearance-split-half r + Spearman-Brown (halves = alternate packages by hash;
                  both halves >=15 turns) — the pooling-honest reliability
  cross-half unit V0: program-split-half (the original SB .43 recipe) | V1: network-split-half
                  (few units; reported with that caveat)
  demographics    corr(score, female), corr(score, party_R)
  agreement       corr(V0, V1) on the double-covered intersection

Output: printed report + data/members/reactivity_v2_study.json (record only; wired NOWHERE)."""
import sqlite3, collections, json, re, math, hashlib
import numpy as np
_s.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT

# --- interaction_features.py verbatim pieces (cutoff + program_of) ---
def program_of(pkg):
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

def sb2(r):
    return 2 * r / (1 + r) if r == r else float('nan')

# --- load turns (rowid order within appearance = the ingest convention) ---
con = sqlite3.connect(B + r'\social_media.db', timeout=60)
con.execute('PRAGMA query_only=1')
app = collections.defaultdict(list)   # (member, package) -> [cutoff-bool]
for m, pkg, t in con.execute("select member, package, text from speeches where source='INTERVIEW' order by rowid"):
    if t and t.strip(): app[(m, pkg)].append(cutoff(t))
total_rows = sum(len(v) for v in app.values())
print('INTERVIEW turns: %d | (member,package) appearances: %d' % (total_rows, len(app)))

def build(grouper, min_turns=12, min_members=8):
    """The shipped aggregation shape: per-(member, group) cells >= min_turns; group >= min_members;
    within-group z of cutoff-rate; member score = turn-weighted mean of per-group z; qualifying
    turns = sum over qualifying cells."""
    cell = collections.defaultdict(lambda: [0, 0])   # (member, group) -> [cut, turns]
    for (m, pkg), flags in app.items():
        g = grouper(pkg)
        c = cell[(m, g)]
        c[0] += sum(flags); c[1] += len(flags)
    rows = collections.defaultdict(dict)             # group -> member -> (rate, turns)
    for (m, g), (cu, n) in cell.items():
        if n >= min_turns: rows[g][m] = (cu / n, n)
    zsum = collections.defaultdict(float); zwt = collections.defaultdict(float); qual = collections.Counter()
    for g, mem in rows.items():
        if len(mem) < min_members: continue
        vals = np.array([r for r, _ in mem.values()])
        mu, sd = vals.mean(), vals.std()
        if sd == 0: continue
        for m, (r, n) in mem.items():
            zsum[m] += ((r - mu) / sd) * n; zwt[m] += n; qual[m] += n
    score = {m: zsum[m] / zwt[m] for m in zsum if zwt[m] > 0}
    return score, qual

def split_half_appearance(grouper):
    """Alternate each member's packages into two halves by stable hash; score each half with the
    full build() machinery; correlate members having >=15 turns in BOTH halves; SB-correct."""
    half = {0: collections.defaultdict(list), 1: collections.defaultdict(list)}
    for (m, pkg), flags in app.items():
        h = int(hashlib.md5(pkg.encode()).hexdigest(), 16) % 2
        half[h][(m, pkg)] = flags
    scores = []
    for hidx in (0, 1):
        global app_save
        sub = half[hidx]
        cellapp = sub
        # rebuild with the same machinery on the half
        cell = collections.defaultdict(lambda: [0, 0])
        for (m, pkg), flags in cellapp.items():
            g = grouper(pkg)
            c = cell[(m, g)]
            c[0] += sum(flags); c[1] += len(flags)
        rows = collections.defaultdict(dict)
        for (m, g), (cu, n) in cell.items():
            if n >= 12: rows[g][m] = (cu / n, n)
        zsum = collections.defaultdict(float); zwt = collections.defaultdict(float); tt = collections.Counter()
        for g, mem in rows.items():
            if len(mem) < 8: continue
            vals = np.array([r for r, _ in mem.values()])
            mu, sd = vals.mean(), vals.std()
            if sd == 0: continue
            for m, (r, n) in mem.items():
                zsum[m] += ((r - mu) / sd) * n; zwt[m] += n; tt[m] += n
        scores.append(({m: zsum[m] / zwt[m] for m in zsum if zwt[m] > 0}, tt))
    (s0, t0), (s1, t1) = scores
    both = [m for m in s0 if m in s1 and t0[m] >= 15 and t1[m] >= 15]
    r = rr([s0[m] for m in both], [s1[m] for m in both])
    return r, sb2(r), len(both)

def unit_split_half(grouper):
    """Group-unit split (the original recipe): alternate GROUPS into halves by hash; member scored
    in each half from its groups there; correlate double-scored members; SB-correct."""
    cell = collections.defaultdict(lambda: [0, 0])
    for (m, pkg), flags in app.items():
        g = grouper(pkg)
        c = cell[(m, g)]
        c[0] += sum(flags); c[1] += len(flags)
    rows = collections.defaultdict(dict)
    for (m, g), (cu, n) in cell.items():
        if n >= 12: rows[g][m] = (cu / n, n)
    groups = [g for g, mem in rows.items() if len(mem) >= 8]
    halves = {0: [], 1: []}
    for g in groups: halves[int(hashlib.md5(g.encode()).hexdigest(), 16) % 2].append(g)
    ss = []
    for hidx in (0, 1):
        zsum = collections.defaultdict(float); zwt = collections.defaultdict(float)
        for g in halves[hidx]:
            mem = rows[g]
            vals = np.array([r for r, _ in mem.values()])
            mu, sd = vals.mean(), vals.std()
            if sd == 0: continue
            for m, (r, n) in mem.items():
                zsum[m] += ((r - mu) / sd) * n; zwt[m] += n
        ss.append({m: zsum[m] / zwt[m] for m in zsum if zwt[m] > 0})
    both = [m for m in ss[0] if m in ss[1]]
    r = rr([ss[0][m] for m in both], [ss[1][m] for m in both])
    return r, sb2(r), len(both)

# --- demographics ---
import moneyball_core as MC
P = MC.party()
gmap = {}
b2n = MC.bio2name()
n2b = {v: k for k, v in b2n.items()}
import csv as _csv
with open(B + r'\fec\legislators.csv', encoding='utf-8') as f:
    for row in _csv.DictReader(f):
        m = b2n.get(row.get('bioguide_id'))
        if m: gmap[m] = row.get('gender', '')

# MEM approximation (the checkpoint rule)
BD = json.load(open(B + r'\behavioral_dispositions.json'))['members']
VV = json.load(open(B + r'\data\members\voteview_consistency.json', encoding='utf-8'))['members']
BEH = ('breadth_z', 'diligence_z', 'visibility_z', 'cooperation_z', 'dominance_z')
MEM = {m for m in BD if m in VV and P.get(m) in ('D', 'R') and all((BD[m] or {}).get(v) is not None for v in BEH)}

rep = {}
for name, grouper in (('V0-per-show (shipped)', program_of), ('V1-per-network', network_of)):
    score, qual = build(grouper)
    cov = {m for m, t in qual.items() if t >= 30}
    memn = len(cov & MEM)
    ar, asb, an = split_half_appearance(grouper)
    ur, usb, un = unit_split_half(grouper)
    dm = [m for m in score if m in gmap and gmap[m] in ('M', 'F')]
    gf = rr([score[m] for m in dm], [1.0 if gmap[m] == 'F' else 0.0 for m in dm])
    dp = [m for m in score if P.get(m) in ('D', 'R')]
    pr = rr([score[m] for m in dp], [1.0 if P.get(m) == 'R' else 0.0 for m in dp])
    rep[name] = dict(covered=len(cov), MEM_N=memn, appearance_sb=(ar, asb, an), unit_sb=(ur, usb, un),
                     gender_r=gf, party_r=pr, scored=len(score))
    print('\n== %s ==' % name)
    print('  covered(>=30 qual): %d | MEM_N: %d (gate 250)' % (len(cov), memn))
    print('  appearance-split-half r=%.3f SB=%.3f (n=%d)' % (ar, asb, an))
    print('  unit-split-half       r=%.3f SB=%.3f (n=%d)%s' % (ur, usb, un, '  [few units: caveat]' if name.startswith('V1') else ''))
    print('  gender loading %.3f | party loading %.3f | scored members %d' % (gf, pr, len(score)))

s0, _ = build(program_of); s1, _ = build(network_of)
bothm = [m for m in s0 if m in s1]
agree = rr([s0[m] for m in bothm], [s1[m] for m in bothm])
print('\nV0-V1 agreement on %d double-scored members: r=%.3f' % (len(bothm), agree))
rep['agreement'] = dict(r=agree, n=len(bothm))
with open(B + r'\data\members\reactivity_v2_study.json', 'w', encoding='utf-8') as f:
    json.dump(rep, f, indent=1, default=str)
print('\nwrote data/members/reactivity_v2_study.json (record only; wired nowhere). Ratification is the user\'s.')
