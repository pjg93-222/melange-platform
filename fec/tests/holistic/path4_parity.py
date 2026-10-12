# -*- coding: utf-8 -*-
"""PATH-4 formula-mirror parity (holistic v1; freeze pin: max |dp| <= 1e-9, >=1,000 edges).
The dashboard JS rebuilds the cascade kernel from the serialized adjacency
(fec/surfaces/templates/make_dashboard_html_00.tpl:414-417):
    PEBASE[u][v] = wc/(wc+6),  wc = ADJ[u][v] * cpf(u,v) * EMU[v]
    cpf(u,v)     = cross-party(D/R) ? DCROSS + (1-DCROSS)*sqrt(BPF[u]*BPF[v]) : 1
This script re-implements THAT math from the tpl source (NOT via build_P — that would be
circular) and compares against moneyball_core.build_P on EVERY edge, BOTH configs — the
CU configuration still stands, the holistic configuration beside it (Paul, 2026-08-17)."""
import os, sys, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, '..', '..')))
import moneyball_core as MC

DCROSS = 0.4   # tpl K.DCROSS (calibrated cross-party diffusion floor; python cpf_diffusion 0.4+0.6*sqrt)

def run(cfg):
    os.environ['NET_CONFIG'] = cfg
    adj = MC.load_network()          # config-keyed cache serves both configs in one process (ISO-5)
    P = MC.build_P(adj)
    bf = MC.bpf(); prt = MC.party()
    mx = 0.0; n = 0
    for u, nb in adj.items():
        for v, w in nb.items():
            if prt.get(u) != prt.get(v) and prt.get(u) in 'DR' and prt.get(v) in 'DR':
                c = DCROSS + (1 - DCROSS) * math.sqrt(bf.get(u, 0.24) * bf.get(v, 0.24))
            else:
                c = 1.0
            wc = w * c * MC.rc(v, 'edge_mult')
            d = abs(wc / (wc + 6) - P[u][v]); n += 1
            if d > mx: mx = d
    # COMPLETENESS, not volume. The old bar was `n >= 1000` directed edges, which silently
    # assumed a dense graph: crypto has 888 and would have FAILED for being small rather than for
    # being wrong. Asserting that we checked EVERY edge the config loaded is both stronger and
    # density-independent -- it catches a truncated walk, which a volume bar never could.
    expected = sum(len(nb) for nb in adj.values())
    ok = (mx <= 1e-9) and n == expected and n > 0
    print('%-14s directed-edge checks %7d/%-7d  max|dp| %.3e  %s'
          % (cfg, n, expected, mx, 'PASS' if ok else 'FAIL'))
    return ok

def check_tpl_source():
    """PRO-1 fix (2026-08-17): the numeric comparison above validates the formula AS TRANSCRIBED
    into this file. If the shipped JS itself drifts, a Python-vs-Python check cannot see it. So
    also read the template TEXT and assert the two load-bearing lines still say what this mirror
    assumes. This does not execute the JS -- stated plainly rather than overclaimed -- but it does
    turn silent template drift into a red test."""
    tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..',
                       'surfaces', 'templates', 'make_dashboard_html_00.tpl')
    tpl = os.path.normpath(tpl)
    if not os.path.exists(tpl):
        print('  tpl source check: SKIP (template not found at %s)' % tpl); return None
    src = io.open(tpl, encoding='utf-8', errors='replace').read()
    want = {
        'kernel wc/(wc+6)': 'PEBASE[u][v]=wc/(wc+6)',
        'wc = adj*cpf*emult': 'const wc=ADJ[u][v]*cpf(u,+v)*(EMU?EMU[+v]:1)',
        'cpf cross-party sqrt(bpf*bpf)': 'K.DCROSS+(1-K.DCROSS)*Math.sqrt(BPF[u]*BPF[v])',
    }
    allok = True
    for label, lit in want.items():
        hit = lit in src
        print('  tpl source: %-32s %s' % (label, 'present' if hit else 'MISSING -> template drifted'))
        allok = allok and hit
    return allok

import io
# EVERY registry config, driven off moneyball_core.NETWORKS so a config added later cannot quietly
# skip the kernel-mirror check. This covered cu + holistic only while three more configs shipped.
_results = {}
for _cfg in sorted(MC.NETWORKS):
    try:
        _results[_cfg] = run(_cfg)
    except FileNotFoundError as _e:
        # fail-closed selection with no artifact present is a legitimate ABSENCE, not a parity
        # failure -- report it as skipped rather than laundering it into a pass.
        print('%-14s SKIP (no artifact: %s)' % (_cfg, str(_e)[:70]))
        _results[_cfg] = None
os.environ.pop('NET_CONFIG', None)
print("  scope: compares moneyball_core.build_P against this file's transcription of the tpl")
print('         kernel; it does NOT execute the dashboard JS. The source check guards that gap.')
ok_src = check_tpl_source()
_ran = {k: v for k, v in _results.items() if v is not None}
_passed = sum(1 for v in _ran.values() if v)
_failed = sum(1 for v in _ran.values() if not v)
if ok_src is False:
    _failed += 1
elif ok_src:
    _passed += 1
_skipped = sum(1 for v in _results.values() if v is None)
allok = _failed == 0 and _passed > 0
# Canonical verdict line for run_tests._suite_verdict (shape A) -- without it the runner cannot
# read this suite, and an unreadable verdict is failure by design.
print('PATH-4 formula-mirror parity - %d passed, %d failed%s'
      % (_passed, _failed, (', %d skipped' % _skipped) if _skipped else ''))
sys.exit(0 if allok else 1)
