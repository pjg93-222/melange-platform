# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""RECEPTIVITY EFFECT-SIZE SENSITIVITY (2026-07-04 — answers 'the .18 scaling and 0.60 retiring
factor are literature-shaped design choices no outcome has ever tested').
No outcome data exists yet to TEST the magnitudes, so this harness does the honest next-best thing:
it BOUNDS them. Every untested constant is swept across a plausible range and the DECISION SURFACE
(per-member flip-model winnability, the thing allocations key off) is recomputed; a constant whose
sweep leaves the top-20/top-50 target sets ~unchanged is decision-robust (its exact value cannot
have bought a wrong decision); a constant that churns the target set is flagged as the one to take
to an outcome test first.
Stage 1 replication gate: pressure_mult and seed_prob are recomputed from the artifact's own stored
components under baseline constants and must match all stored values exactly — proves the sweep
formulas carry no drift before any variant is trusted.
Prospective outcome test (pre-registered design, run when data exists): correlate seed_prob with
NEW cosponsorships adopted next quarter (Chen/SNAM seeding claim), and pressure_mult with stance-tier
movement among members the user's In-contact log shows were actually engaged (Byrne/F&H claim).
Usage: python fec/members/receptivity_sensitivity.py"""
import json, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
clip = lambda x, lo, hi: max(lo, min(hi, x))
RJ = json.load(open(B + r'\member_receptivity.json'))
M = RJ['members']
PW = 0.30   # persona weight (receptivity_layer PERSONA_W default)

def pressure(m, k_susc=0.18, pt_loss=1.15, pt_gain=0.92, ret=0.60, pw=PW, inc='incent_mult'):
    d = M[m]
    st = d['susc_trait_z']
    pz = d.get('persona_susc_z')
    susc = (1 - pw) * st + pw * pz if pz is not None else st
    susc_mult = clip(1 + k_susc * susc, 0.70, 1.30)
    incent = ret if d.get('retiring') else d[inc]   # R1 (D-107): inc='incent_mult' is the dial-off F&H value; with COMPET_MULT on the sweep passes MC.incent_field() ('incent_mult_compet', competitiveness term neutral)
    pt = pt_loss if d['pt_domain'] == 'loss' else pt_gain
    return clip(susc_mult * incent * pt, 0.50, 1.60)

CSLOT = RJ.get('coop_slot', 'v1')   # A-slot basis (audit 2026-07-10 S3-03): mirror the engine's
                                    # promoted slot — v2 reads cooperation_v2_z (missing -> neutral 0,
                                    # never a v1 fallback), v1 reads the legacy cooperation_z.
def _coop(d):
    return ((d.get('cooperation_v2_z') if CSLOT == 'v2' else d.get('cooperation_z')) or 0.0)

def seed(m, kc=0.10, kb=0.06, kv=0.04):
    d = M[m]
    return clip(0.60 + kc * _coop(d)
                     + kb * (d.get('breadth_z') or 0.0)
                     + kv * (d.get('visibility_z') or 0.0), 0.30, 0.90)

# ---- stage 1: replication gate ----
bad_p = [m for m in M if abs(pressure(m) - M[m]['pressure_mult']) > 0.0011]
bad_s = [m for m in M if abs(seed(m) - M[m]['seed_prob']) > 0.0011]
if RJ.get('basis') != 'behavioral':
    print('!! artifact basis=%s — this harness expects the behavioral basis; aborting' % RJ.get('basis')); sys.exit(1)
if bad_p or bad_s:
    print('!! REPLICATION GATE FAILED (pressure %d, seed %d mismatches) — formulas drifted; fix before sweeping'
          % (len(bad_p), len(bad_s)))
    for m in (bad_p + bad_s)[:5]: print('   ', m, M[m]['pressure_mult'], round(pressure(m), 3))
    sys.exit(1)
print('replication gate: %d/%d pressure_mult and seed_prob reproduced exactly — sweep formulas are drift-free\n' % (len(M), len(M)))

# ---- decision surface: flip-model winnability under variant pressure_mult ----
import moneyball_core as MC
MC.receptivity()                              # prime cache from disk
INC = MC.incent_field()                       # R1 (D-107): dial OFF -> 'incent_mult' (identical to the gate above); ON -> 'incent_mult_compet'
if any(abs(pressure(m, inc=INC) - M[m][MC.pressure_field()]) > 0.0011 for m in M):
    print('!! REPLICATION GATE FAILED (%s from %s) - sweep baseline does not match the field the engines read' % (MC.pressure_field(), INC)); sys.exit(1)
BASE_REC = {m: dict(v) for m, v in MC._c['rec'].items()}
from engine import flip_model as FM           # one import; rc() reads MC._c['rec'] live at call time

def winnability():
    wn = collections.Counter()
    for c in FM.cats:
        cs, campT, nA, nB, mAt, mBt, tgt = FM.topic_setup(c, FM.CS0)
        for n in FM.members:
            wn[n] += tgt[n] * FM.pflip(FM.cu0[n], FM.ind0[n], FM.R[n], nA[n], nB[n], mAt, mBt,
                                       FM.rc(n, 'pressure_mult'), FM.CM[n])   # COMPET_MULT: same c as flip_model (dial OFF -> 1.0)
    return wn

def swap(variant_pm):
    rec = {m: dict(BASE_REC.get(m, {})) for m in M}
    for m in rec: rec[m][MC.pressure_field()] = variant_pm[m]   # R1 (Paul ruling 2026-10-07): the field the engines read for the current COMPET_MULT position
    MC._c['rec'] = rec

base_wn = winnability()
base20 = {n for n, _ in base_wn.most_common(20)}; base50 = {n for n, _ in base_wn.most_common(50)}

def spearman(a, b):
    ka = {n: i for i, (n, _) in enumerate(a.most_common())}
    kb = {n: i for i, (n, _) in enumerate(b.most_common())}
    ns = list(ka); n = len(ns)
    d2 = sum((ka[x] - kb[x]) ** 2 for x in ns)
    return 1 - 6 * d2 / (n * (n * n - 1))

VARIANTS = [
    ('susc scale .18 -> .09 (halved)',   dict(k_susc=0.09)),
    ('susc scale .18 -> .27 (x1.5)',     dict(k_susc=0.27)),
    ('PT 1.15/.92 -> 1.10/.95 (mild)',   dict(pt_loss=1.10, pt_gain=0.95)),
    ('PT 1.15/.92 -> 1.25/.85 (strong)', dict(pt_loss=1.25, pt_gain=0.85)),
    ('retiring 0.60 -> 0.45',            dict(ret=0.45)),
    ('retiring 0.60 -> 0.80',            dict(ret=0.80)),
    ('retiring 0.60 -> 1.00 (off)',      dict(ret=1.00)),
    ('persona weight .30 -> 0 (off)',    dict(pw=0.0)),
    ('persona weight .30 -> .45',        dict(pw=0.45)),
]
print('%-36s %s' % ('pressure_mult constant sweep', 'top20ovl  top50ovl  rank-rho  |verdict (decisions)'))
results = []
for label, kw in VARIANTS:
    pm = {m: pressure(m, inc=INC, **kw) for m in M}
    swap(pm)
    wn = winnability()
    o20 = len(base20 & {n for n, _ in wn.most_common(20)})
    o50 = len(base50 & {n for n, _ in wn.most_common(50)})
    rho = spearman(base_wn, wn)
    verdict = 'ROBUST' if o20 >= 18 else ('MODERATE' if o20 >= 15 else 'SENSITIVE')
    results.append((label, o20, o50, rho, verdict))
    print('%-36s %5d/20  %6d/50  %8.4f  |%s' % (label, o20, o50, rho, verdict))
MC._c['rec'] = BASE_REC   # restore

# ---- seed_prob sweep: gate-level analytics (cascade MC not rerun — noted honestly) ----
print('\nseed_prob coefficient sweep (gate analytics; cascades not rerun):')
from moneyball_core import SGATE
CS = json.load(open(B + r'\category_stance.json'))['matrix']
for label, kw in [('coefs halved (.05/.03/.02)', dict(kc=0.05, kb=0.03, kv=0.02)),
                  ('coefs x1.5 (.15/.09/.06)',   dict(kc=0.15, kb=0.09, kv=0.06))]:
    ds = [abs(seed(m, **kw) - M[m]['seed_prob']) for m in M]
    big = sum(1 for d in ds if d > 0.05)
    print('  %-28s mean |dseed| %.3f | max %.3f | members moved >0.05: %d/%d'
          % (label, sum(ds) / len(ds), max(ds), big, len(ds)))
print('\nInterpretation: ROBUST = the constant cannot have changed WHO gets targeted (top-20 stable');
print('across its plausible range); SENSITIVE = this constant is the one to outcome-test first.')
