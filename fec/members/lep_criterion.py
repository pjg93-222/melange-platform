import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""LEGISLATIVE EFFECTIVENESS PROJECT CRITERION — Volden & Wiseman (Center for Effective Lawmaking,
CEL House+Senate 93rd-118th; user-approved pull, run-4l 2026-07-05). Two jobs:

(A) THE PAPER-EXACT RKH CONSCIENTIOUSNESS CRITERION. RKH PSRM 2016 Table 3: more-Conscientious
    members propose FEWER ceremonial/symbolic bills (binomial logit C = -0.39...-0.58, minority-
    concentrated). Run-4k found the old "C -> committee_leader" criterion mis-sourced (Committee
    Chair is a CONTROL in their table). Here: per-member CEREMONIAL SHARE of sponsored bills
    (classic-methodology counts, cbill1/allbill1), pooled over Congresses 115-118, floor >=8 bills.
    Scored against diligence_z (behavioral basis), lexical C (v1), and behavior-calibrated C (v2).

(B) EXTERNAL EFFECTIVENESS CONVERGENCE. lesclassic (117-118 mean; else latest Congress served)
    vs the model's blended score — the established external measure should NOT be orthogonal to a
    score whose legislative pillar prices the same machinery. (Perfect agreement NOT expected:
    LES rewards majority/chair status; our score prices CU-alignment, money and network too.)

PRE-REGISTERED BARS (locked before first scoring run; Spearman):
  A: rho <= -0.20 PASS | (-0.20,-0.10] directional | (-0.10,+0.10) null | >= +0.10 CONTRARY
  B: rho >= +0.30 convergent PASS | [+0.15,+0.30) weak | < +0.15 null
Bonus (no hypothesis): stores dwnom1 per member — the DW-NOMINATE node attribute the user's
Gaynor/Bonica bib notes ask for. The static follow-through component is consumed by
behavioral_dispositions.py; it never enters rank directly.
Data: fec/lep/*.dta (gitignored, like fec/liwc/rkh/); refresh via thelawmakers.org data page."""
import json, sys, os
import moneyball_core as MBC
from moneyball_core import dump_atomic
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import pandas as pd
B = MB_ROOT
LEP = os.path.join(B, 'fec', 'lep')   # data at ROOT (live tree), like fec/liwc/rkh
WINDOW = (115, 116, 117, 118)   # ceremonial-share pooling window
MINBILLS = 8                    # share-stability floor (RKH weighted by trials; we floor + rank)

C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
bg2name = {(m.get('bioguide') or ''): m['member'] for m in C.values() if m.get('bioguide')}
h = pd.read_stata(os.path.join(LEP, 'CELHouse93to118Reduced.dta'))
s = pd.read_stata(os.path.join(LEP, 'CELSenate93to118Reduced.dta'))
lep = pd.concat([h, s], ignore_index=True)
lep = lep[lep['bioguide_id'].astype(str).str.len() > 0]
lep['member'] = lep['bioguide_id'].map(bg2name)
lep = lep[lep['member'].notna()]
print('LEP rows matched to current roster: %d rows / %d members (of 533; 119th freshmen have no LEP history)'
      % (len(lep), lep['member'].nunique()))

per = {}
for m, g in lep.groupby('member'):
    gw = g[g['congress'].isin(WINDOW)]
    tot = float(gw['allbill1'].fillna(0).sum()); cer = float(gw['cbill1'].fillna(0).sum())
    share = (cer / tot) if tot >= MINBILLS else None
    g1718 = g[g['congress'].isin((117, 118))]
    lc = float(g1718['lesclassic'].mean()) if len(g1718) else None
    gl = g.sort_values('congress').iloc[-1]
    if lc is None or lc != lc: lc = float(gl['lesclassic']) if gl['lesclassic'] == gl['lesclassic'] else None
    # follow-through (run-4w, user-ratified diligence input): committee-action-and-beyond stage
    # events per bill introduced (allaic1+allabc1)/allbill1 — the shepherd-vs-introduce-and-forget
    # C-behavior. Can exceed 1 (a bill advancing through both stages counts twice). None under 5 bills.
    acted = float(gw['allaic1'].fillna(0).sum()) + float(gw['allabc1'].fillna(0).sum())
    ft = (acted / tot) if tot >= 5 else None
    per[m] = {'ceremonial_share': (round(share, 4) if share is not None else None),
              'bills_115_118': int(tot), 'followthrough': (round(ft, 4) if ft is not None else None),
              'lesclassic': (round(lc, 4) if lc is not None and lc == lc else None),
              'les2_latest': (round(float(gl['les2']), 4) if gl['les2'] == gl['les2'] else None),
              'dwnom1': (round(float(gl['dwnom1']), 4) if gl['dwnom1'] == gl['dwnom1'] else None),
              'latest_congress': int(gl['congress'])}

def spearman(d1, d2):
    ks = [k for k in d1 if k in d2 and d1[k] is not None and d2[k] is not None]
    n = len(ks)
    if n < 20: return None, n
    def ranks(d):
        sv = sorted(ks, key=lambda k: d[k]); r = {}; i = 0
        while i < len(sv):
            j = i
            while j + 1 < len(sv) and d[sv[j + 1]] == d[sv[i]]: j += 1
            for t in range(i, j + 1): r[sv[t]] = (i + j) / 2.0
            i = j + 1
        return r
    r1, r2 = ranks(d1), ranks(d2); mu = (n - 1) / 2.0
    num = sum((r1[k] - mu) * (r2[k] - mu) for k in ks)
    den = (sum((r1[k] - mu) ** 2 for k in ks) * sum((r2[k] - mu) ** 2 for k in ks)) ** 0.5 or 1.0
    return num / den, n

share = {m: v['ceremonial_share'] for m, v in per.items()}
disp = json.load(open(B + r'\behavioral_dispositions.json'))['members']
dil = {m: d.get('diligence_z') for m, d in disp.items()}
v1 = json.load(open(B + r'\data\members\big5_traits.json'))['traits']; c1 = {m: d.get('C') for m, d in v1.items()}
v2 = json.load(open(B + r'\data\members\big5_v2.json'))['traits']; c2 = {m: d.get('C') for m, d in v2.items()}

def bandA(r):
    if r is None: return 'insufficient n'
    if r <= -0.20: return 'PASS (RKH-consistent)'
    if r <= -0.10: return 'directional support'
    if r < 0.10: return 'null'
    return 'CONTRARY'
print('\n[A] Paper-exact RKH C criterion — ceremonial share of sponsored bills (LOWER = more conscientious behavior):')
resA = {}
for label, d in (('diligence_z (behavioral basis)', dil), ('C v1 (lexical)', c1), ('C v2 (behavior-calibrated)', c2)):
    rho, n = spearman(share, d)
    resA[label] = {'rho': (round(rho, 4) if rho is not None else None), 'n': n, 'verdict': bandA(rho)}
    print('  %-34s rho=%s (n=%d) -> %s' % (label, ('%+.3f' % rho) if rho is not None else ' n/a', n, bandA(rho)))

lc = {m: v['lesclassic'] for m, v in per.items()}
score = MBC.scores()
rho_b, n_b = spearman(lc, score)
verB = ('PASS (convergent)' if rho_b >= 0.30 else 'weak convergence' if rho_b >= 0.15 else 'null') if rho_b is not None else 'insufficient n'
print('\n[B] External effectiveness convergence — lesclassic vs model blended score:')
print('  rho=%+.3f (n=%d) -> %s' % (rho_b, n_b, verB))
# legislative pillar from the PYTHON RANK AUTHORITY (ranks_python.json pcts.leg — the workbook's
# Names/pillar columns are live formulas whose caches are empty until Excel next opens the file)
resB = {'vs_blended_score': {'rho': round(rho_b, 4), 'n': n_b, 'verdict': verB}}
try:
    pcts = json.load(open(B + r'\ranks_python.json'))['pcts']
    legp = {m: d.get('leg') for m, d in pcts.items()}
    rho_l, n_l = spearman(lc, legp)
    resB['vs_legislative_pillar'] = {'rho': (round(rho_l, 4) if rho_l is not None else None), 'n': n_l}
    print('  legislative pillar (ranks_python pcts.leg):  rho=%s (n=%d)' % (('%+.3f' % rho_l) if rho_l is not None else ' n/a', n_l))
except (FileNotFoundError, KeyError) as e:
    print('  (legislative pillar unavailable: %r)' % e)

vals = sorted(v for v in share.values() if v is not None)
nshare = len(vals)
zpct = 100 * sum(1 for x in vals if x == 0) / max(1, nshare)
dist = {'zeros_pct': round(zpct, 1), 'median': round(vals[nshare // 2], 4) if nshare else None,
        'p90': round(vals[int(0.9 * nshare)], 4) if nshare else None, 'max': round(vals[-1], 4) if nshare else None}
print('\nceremonial-share distribution: %d members, %.0f%% zeros, median %.1f%%, p90 %.1f%% — modern Congresses'
      % (nshare, zpct, 100 * (dist['median'] or 0), 100 * (dist['p90'] or 0)))
print('  carry few V-W ceremonial bills (House banned commemoratives in 1995), so rank tests are ATTENUATED')
print("  vs RKH's 104th-112th binomial setting — a null here is informative but not decisive.")
dump_atomic({'schema': 'lep-criterion-v2', 'status': 'ACTIVE_STATIC_CRITERION',
    'window': list(WINDOW), 'min_bills': MINBILLS, 'members_matched': len(per),
    'members_with_share': nshare,
    'prereg_bars': {'A': 'rho<=-0.20 PASS | <=-0.10 directional | (-0.10,0.10) null | >=+0.10 contrary',
                    'B': 'rho>=+0.30 PASS | >=+0.15 weak | else null'},
    'share_distribution': dist,
    'results_A_ceremonial_vs_C': resA, 'results_B_les_vs_score': resB, 'per_member': per,
    'consumers': ['behavioral_dispositions.py', 'make_member_desk.py', 'stance/say_do_gap.py'],
    'provenance': ('CEL/Volden-Wiseman LES 93-118 (classic counts, methodology-1); RKH PSRM 2016 '
                   'Table-3 paper-exact C criterion; static through the 118th; consumed as one '
                   'legislative-follow-through component but never directly as a rank input')},
    B + r'\data\members\lep_criterion.json')
print('\nwrote data/members/lep_criterion.json (%d members matched, %d with ceremonial share; dwnom1 stored per member as node-attribute bonus)' % (len(per), nshare))
