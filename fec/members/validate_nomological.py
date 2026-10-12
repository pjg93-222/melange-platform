# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""NOMOLOGICAL VALIDATION — one-shot test per big5_nomological_prereg.md (LOCKED before this ran).
Label-free external validity (ValiTex V.6): trait instruments vs behavioral criteria, one-tailed
directional tests, BH-FDR within the primary family, three specification variants, discriminant
requirements, r_alerting pattern statistic. Read-only; prints the verdict table."""
import csv, json, math, os, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
COMPOSITE = os.environ.get('B5_COMPOSITE',os.path.join(B,'source_data','big5_composite.json'))

PANEL = json.load(open(B + r"\behavior_panel.json"))["members"]
C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
b2p = {}
for r in csv.DictReader(open(os.path.join(B, 'votes', 'HS119_members.csv'), encoding='utf-8')):
    bg = (r.get('bioguide_id') or '').strip(); pc = (r.get('party_code') or '').strip()
    if bg and pc in ('100', '200'): b2p[bg] = 'R' if pc == '200' else 'D'
party = {m: b2p.get((C.get(m) or {}).get('bioguide') or '') for m in C}
chamber = {m: (C.get(m) or {}).get('chamber') or '?' for m in C}
extremity = {m: PANEL.get(m, {}).get('nominate_extremity') for m in PANEL}

def pearson(a, b):
    n = len(a)
    if n < 3: return 0.0, 1.0, n
    ma = sum(a)/n; mb = sum(b)/n
    sa = math.sqrt(sum((x-ma)**2 for x in a)); sb = math.sqrt(sum((x-mb)**2 for x in b))
    if sa == 0 or sb == 0: return 0.0, 1.0, n
    r = sum((x-ma)*(y-mb) for x, y in zip(a, b)) / (sa*sb)
    # one-tailed p via t (positive direction assumed; caller flips for negative predictions)
    t = r * math.sqrt(max(1, n-2) / max(1e-12, 1-r*r))
    # normal approx of t for large n
    p = 0.5 * math.erfc(t / math.sqrt(2))
    return r, p, n

def zlog_within_chamber(vals):
    """vals: {m: raw}. log1p then z within chamber."""
    out = {}
    for ch in ('House', 'Senate'):
        ms = [m for m in vals if chamber.get(m) == ch]
        if len(ms) < 5: continue
        xs = {m: math.log1p(max(0.0, float(vals[m]))) for m in ms}
        mu = sum(xs.values())/len(xs)
        sd = (sum((v-mu)**2 for v in xs.values())/max(1, len(xs)-1))**0.5 or 1.0
        for m in ms: out[m] = (xs[m]-mu)/sd
    return out

COUNTS = {'cosponsor_degree', 'n_cosponsored', 'n_sponsored', 'votes_cast',
          'n_floor_speeches', 'floor_words', 'n_interviews', 'n_interview_programs', 'n_tweets'}
def criterion(name):
    vals = {m: d[name] for m, d in PANEL.items() if d.get(name) is not None}
    return zlog_within_chamber(vals) if name in COUNTS else {m: float(v) for m, v in vals.items()}

# ---- instruments ----
INSTR = {}
INSTR['L'] = {m: d for m, d in json.load(open(B + r"\data\members\big5_traits.json"))['traits'].items()
              if d.get('tokens', 0) >= 1500}
gp = COMPOSITE
if os.path.exists(gp): INSTR['G'] = json.load(open(gp))['traits']
INSTR['P'] = {m: d for m, d in json.load(open(B + r"\data\members\agency_communion.json"))['members'].items()
              if d.get('n', 0) >= 15}
ip = B + r"\data\lobbying\interaction_features.json"
if os.path.exists(ip): INSTR['X'] = {m: d for m, d in json.load(open(ip))['members'].items() if d.get('turns', 0) >= 30}

TRAIT_KEY = {'A': 'A', 'C': 'C', 'E': 'E', 'ES': 'ES', 'O': 'O',
             'Agency': 'agency', 'Communion': 'communion', 'wpt': 'wpt_mean'}

PRIMARY = [  # (id, trait, instruments, criterion, sign, strong?)
    (1, 'A', ('L', 'G'), 'bipartisanship', +1, True),
    (2, 'A', ('L', 'G'), 'cosponsor_degree', +1, False),
    (3, 'A', ('L', 'G'), 'n_cosponsored', +1, False),
    (4, 'C', ('L', 'G'), 'votes_cast', +1, True),
    (5, 'C', ('L', 'G'), 'committee_leader', +1, False),
    (6, 'C', ('L', 'G'), 'n_sponsored', +1, True),
    (7, 'E', ('L', 'G'), 'n_sponsored', +1, True),
    (8, 'ES', ('L', 'G'), 'n_sponsored', +1, True),
    (9, 'Agency', ('P',), 'committee_leader', +1, True),
    (10, 'Agency', ('P',), 'n_sponsored', +1, False),
    (11, 'Communion', ('P',), 'bipartisanship', +1, True),
    (12, 'Communion', ('P',), 'cosponsor_degree', +1, False),
    (13, 'Communion', ('P',), 'n_cosponsored', +1, False),
]
SECONDARY = [
    ('E', ('L', 'G'), 'n_interviews', +1), ('E', ('L', 'G'), 'n_tweets', +1),
    ('E', ('L', 'G'), 'n_floor_speeches', +1), ('Agency', ('P',), 'n_interviews', +1),
    ('wpt', ('X',), 'n_interviews', +1),
]

def trait_vec(inst, trait):
    key = TRAIT_KEY[trait]
    return {m: float(d[key]) for m, d in INSTR.get(inst, {}).items() if key in d}

def run_test(inst, trait, crit_name, sign, variant):
    tv = trait_vec(inst, trait); cv = criterion(crit_name)
    ms = [m for m in tv if m in cv]
    if variant == 'partial':   # residualize both on extremity
        ms = [m for m in ms if extremity.get(m) is not None]
        if len(ms) < 20: return None
        ex = [extremity[m] for m in ms]
        def resid(vec):
            r_, _, _ = pearson([vec[m] for m in ms], ex)
            mu_v = sum(vec[m] for m in ms)/len(ms); mu_e = sum(ex)/len(ex)
            sd_v = (sum((vec[m]-mu_v)**2 for m in ms)/max(1,len(ms)-1))**0.5 or 1.0
            sd_e = (sum((e-mu_e)**2 for e in ex)/max(1,len(ms)-1))**0.5 or 1.0
            return {m: (vec[m]-mu_v)/sd_v - r_*((extremity[m]-mu_e)/sd_e) for m in ms}
        tv2, cv2 = resid(tv), resid(cv)
        r, p, n = pearson([tv2[m] for m in ms], [cv2[m] for m in ms])
    elif variant == 'within-party':
        rs = []
        for pt in ('D', 'R'):
            pm = [m for m in ms if party.get(m) == pt]
            if len(pm) < 20: continue
            r_, _, _ = pearson([tv[m] for m in pm], [cv[m] for m in pm])
            rs.append(r_)
        if not rs: return None
        r = sum(rs)/len(rs); n = len(ms)
        t = r * math.sqrt(max(1, n-2) / max(1e-12, 1-r*r))
        p = 0.5 * math.erfc(t / math.sqrt(2))
    else:
        if len(ms) < 20: return None
        r, p, n = pearson([tv[m] for m in ms], [cv[m] for m in ms])
    if sign < 0: r, p = -r, p  # flip so predicted direction is positive; p recomputed below
    t = r * math.sqrt(max(1, n-2) / max(1e-12, 1-r*r))
    p = 0.5 * math.erfc(t / math.sqrt(2))
    return {'r': r, 'p': p, 'n': n}

def bh_fdr(pvals, q=0.05):
    idx = sorted(range(len(pvals)), key=lambda i: pvals[i])
    passed = [False]*len(pvals); mx = -1
    for rank, i in enumerate(idx, 1):
        if pvals[i] <= q*rank/len(pvals): mx = rank
    for rank, i in enumerate(idx, 1):
        if rank <= mx: passed[i] = True
    return passed

print("NOMOLOGICAL VALIDATION — pre-registered one-shot (see big5_nomological_prereg.md)\n")
for inst in ('L', 'G', 'P'):
    tests = [(tid, tr, cr, sg, strong) for tid, tr, insts, cr, sg, strong in PRIMARY if inst in insts]
    if not tests or inst not in INSTR: continue
    rows = []
    for tid, tr, cr, sg, strong in tests:
        res = {v: run_test(inst, tr, cr, sg, v) for v in ('raw', 'partial', 'within-party')}
        if not res['raw']: continue
        rows.append((tid, tr, cr, strong, res))
    ps = [r[4]['raw']['p'] for r in rows]
    fdr = bh_fdr(ps)
    hits = sum(1 for r in rows if r[4]['raw']['r'] > 0)
    pred = [0.20 if r[3] else 0.15 for r in rows]
    obs = [r[4]['raw']['r'] for r in rows]
    ral, _, _ = pearson(pred, obs) if len(set(pred)) > 1 else (float('nan'), 1, 0)
    print("=== INSTRUMENT %s — %d primary tests | sign hits %d/%d (%.0f%%) | r_alerting %.2f" % (
        inst, len(rows), hits, len(rows), 100*hits/len(rows), ral))
    for k, (tid, tr, cr, strong, res) in enumerate(rows):
        ra = res['raw']; rp = res['partial']; rw = res['within-party']
        print("  #%-2d %-10s -> %-18s %s r=%+.3f (n=%d, p=%.3f%s)  partial %+0.2f  within-party %+0.2f" % (
            tid, tr, cr, 'STRONG' if strong else '      ', ra['r'], ra['n'], ra['p'],
            ', FDR-sig' if fdr[k] else '',
            rp['r'] if rp else float('nan'), rw['r'] if rw else float('nan')))
    print()

# discriminant requirements
print("=== DISCRIMINANT (required):")
def rr(inst, trait, cr):
    res = run_test(inst, trait, cr, +1, 'raw')
    return res['r'] if res else float('nan')
d1a, d1c = rr('P', 'Agency', 'committee_leader'), rr('P', 'Communion', 'committee_leader')
d2c, d2a = rr('P', 'Communion', 'bipartisanship'), rr('P', 'Agency', 'bipartisanship')
print("  D1 agency>communion on committee_leader: %+0.3f vs %+0.3f  %s" % (d1a, d1c, 'PASS' if d1a > d1c else 'FAIL'))
print("  D2 communion>agency on bipartisanship:   %+0.3f vs %+0.3f  %s" % (d2c, d2a, 'PASS' if d2c > d2a else 'FAIL'))

print("\n=== SECONDARY (text-contaminated, descriptive only):")
for tr, insts, cr, sg in SECONDARY:
    for inst in insts:
        if inst not in INSTR: continue
        res = run_test(inst, tr, cr, sg, 'raw')
        if res: print("  %s:%-9s -> %-18s r=%+.3f (n=%d)" % (inst, tr, cr, res['r'], res['n']))
