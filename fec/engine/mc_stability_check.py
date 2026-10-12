# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""MC-budget stability check (motivated by Almazaydeh et al., Sci Rep 2026, s41598-026-47299-8):
noisy Monte-Carlo marginal estimates can corrupt lazy-greedy seed selection (their CELF++ at 50
sims/estimate captured only 11.2%% of the sampling-based spread). Moneyball's frontier runs MC=20 but
with an edge-keyed CRN, so competing candidates are always compared under IDENTICAL draws -- their
exact failure mode (independently-noisy estimates) cannot occur. The residual risk is subtler: the
picked seed set could be overfit to one seed's particular 20 cascade realizations. This script measures
that directly: rebuild a single-topic frontier under a higher MC budget and under different RNG seeds,
then compare the picked seed sequences.  Usage:  [TOPIC=Interchange] python fec/engine/mc_stability_check.py"""
import json, os, sys, subprocess
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT; FE = B + r'\fec\engine'
TOPIC = os.environ.get('TOPIC', 'Interchange')
RUNS = [('MC=20  SEED=7 (production)', {'MC':'20','SEED':'7'}),
        ('MC=100 SEED=7 (5x MC budget)', {'MC':'100','SEED':'7'}),
        ('MC=20  SEED=1 (different RNG)', {'MC':'20','SEED':'1'}),
        ('MC=20  SEED=13 (different RNG)', {'MC':'20','SEED':'13'})]
def frontier(env):
    out = B + r'\_mcstab_%s_%s.json' % (env['MC'], env['SEED'])
    e = dict(os.environ, TOPIC=TOPIC, FRONTIER_OUT=out, **env)
    subprocess.run([sys.executable, FE + r'\budget_frontier.py'], capture_output=True, text=True,
                   encoding='utf-8', errors='replace', env=e)
    d = json.load(open(out)); os.remove(out)
    t = d['topics'][TOPIC]
    seeds = [p['seed'] for p in t['path'] if p.get('seed')]
    return seeds, t['path'][-1]['spread'], t.get('knee')
res = []
for name, env in RUNS:
    s, sp, kn = frontier(env); res.append((name, s, sp, kn))
    print('%-32s %2d seeds | final spread %.1f | knee $%s' % (name, len(s), sp, f'{kn:,}' if kn else '?'))
base = res[0][1]; bset = set(base)
print('\nAgreement vs production (MC=20 SEED=7, %d seeds):' % len(base))
for name, s, sp, kn in res[1:]:
    ss = set(s); inter = bset & ss
    jac = len(inter) / max(1, len(bset | ss))
    pref = 0
    for a, b in zip(base, s):
        if a == b: pref += 1
        else: break
    print('  %-32s set-overlap %.0f%% (Jaccard) | first-%d picks identical | spread delta %+.1f'
          % (name, 100*jac, pref, sp - res[0][2]))
print('\nInterpretation: high set-overlap at MC=100 (same seed) and across RNG seeds => the MC=20+CRN')
print('frontier is NOT overfit to its particular draws; low overlap => raise the frontier MC budget.')
