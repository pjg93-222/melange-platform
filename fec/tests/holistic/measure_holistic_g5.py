# -*- coding: utf-8 -*-
"""Gate-5 measurement (holistic v1): weight/degree/p-distributions per config (party-stratified),
D8 trigger evaluation (DIAGNOSTIC cross-config comparison — calibration input, never a shipped
claim), MEA-3 role-mass shares from the BASE subtotals, MEA-4 admitted-per-layer counts, MEA-5
isolates. One process, both configs, through the config-keyed loaders. Writes g5_measurement.json."""
import os, sys, json, statistics
HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(WT, 'fec'))
os.environ.pop('NET_CONFIG', None)
import moneyball_core as MC
import roster

DEN = roster.count() * (roster.count() - 1) // 2  # roster pairs

def tail(xs, t): return round(sum(1 for x in xs if x > t) / len(xs), 6) if xs else None
def med(xs): return round(statistics.median(xs), 6) if xs else None

def measure(holistic):
    if holistic: os.environ['NET_CONFIG'] = 'holistic'
    else: os.environ.pop('NET_CONFIG', None)
    adj = MC.load_network(); P = MC.build_P(adj); prt = MC.party()
    W, Pp = [], []
    Wx = {'within': [], 'cross': []}; Px = {'within': [], 'cross': []}
    for u, nb in adj.items():
        for v, w in nb.items():
            if u < v:
                s = 'cross' if (prt.get(u) != prt.get(v) and prt.get(u) in 'DR' and prt.get(v) in 'DR') else 'within'
                W.append(w); Wx[s].append(w)
    for u, nb in P.items():
        for v, p_ in nb.items():
            if u < v:
                s = 'cross' if (prt.get(u) != prt.get(v) and prt.get(u) in 'DR' and prt.get(v) in 'DR') else 'within'
                Pp.append(p_); Px[s].append(p_)
    deg = [len(nb) for nb in adj.values()]
    return {'netfile': MC._c.get('netfile@holistic' if holistic else 'netfile'),
            'pairs': len(W), 'density_pct_of_%d' % DEN: round(100.0 * len(W) / DEN, 2),
            'weight': {'median': med(W), 'p90': round(sorted(W)[int(0.9 * len(W))], 4), 'max': round(max(W), 4)},
            'degree': {'median': med(deg), 'max': max(deg)},
            'p': {'median': med(Pp), 'tail_gt_0.5': tail(Pp, 0.5), 'tail_gt_0.8': tail(Pp, 0.8),
                  'tail_gt_0.9': tail(Pp, 0.9), 'tail_gt_0.99': tail(Pp, 0.99)},
            'p_by_party_stratum': {s: {'n': len(Px[s]), 'median': med(Px[s]), 'tail_gt_0.5': tail(Px[s], 0.5)} for s in Px}}

cu = measure(False); hol = measure(True)
r_tail = (hol['p']['tail_gt_0.5'] / cu['p']['tail_gt_0.5']) if cu['p']['tail_gt_0.5'] else None
d_med = abs((hol['p']['median'] or 0) - (cu['p']['median'] or 0))
d8 = {'tail_ratio_gt0.5_hol_over_cu': round(r_tail, 3) if r_tail else None,
      'median_p_delta_abs': round(d_med, 4),
      'trigger_tail_ratio_gt_2x': bool(r_tail and r_tail > 2), 'trigger_median_delta_gt_0.1': d_med > 0.1,
      'fired': bool((r_tail and r_tail > 2) or d_med > 0.1),
      'label': 'DIAGNOSTIC cross-config calibration comparison (different universes + role weights); feeds the D8 wscale ruling; never a shipped claim'}

import openpyxl
dy = {}
ws = openpyxl.load_workbook(os.path.join(WT, 'holistic_network_BASE_20260817.xlsx'), read_only=True, data_only=True)['Edges']
b2n = {}
for r_ in ws.iter_rows(min_row=2, values_only=True):
    k = (min(r_[0], r_[1]), max(r_[0], r_[1]))
    e = dy.setdefault(k, [0.0, 0.0])
    e[0] += r_[8]; e[1] += r_[9]
    b2n[r_[0]] = r_[6]; b2n[r_[1]] = r_[7]
shares = [0.2 * wr / (1.0 * wo + 0.2 * wr) for wo, wr in dy.values() if (wo + wr) > 0]
top = sorted(dy.items(), key=lambda kv: -(kv[1][0] + 0.2 * kv[1][1]))[:10]
mea3 = {'dyads': len(dy), 'regular_mass_share': {'median': med(shares), 'p25': round(sorted(shares)[len(shares)//4], 4),
        'p75': round(sorted(shares)[3*len(shares)//4], 4), 'gt_0.5_frac': tail(shares, 0.5)},
        'trigger_median_gt_0.5': (statistics.median(shares) > 0.5),
        'top10_dyads_by_weight': [{'pair': [b2n.get(a, a), b2n.get(b, b)],
                                   'weight': round(wo + 0.2 * wr, 3),
                                   'regular_share': round(0.2 * wr / (wo + 0.2 * wr), 3)}
                                  for (a, b), (wo, wr) in top]}

man = json.load(open(os.path.join(WT, 'holistic_network_AUG_20260817.manifest.json'), encoding='utf-8'))
base_man = json.load(open(os.path.join(WT, 'holistic_network_BASE_20260817.manifest.json'), encoding='utf-8'))
out = {'cu_DIAGNOSTIC_REFERENCE': cu, 'holistic': hol, 'd8_trigger_eval': d8, 'mea3_role_mass': mea3,
       'mea4_admitted_per_layer': man['admitted'], 'mea5_isolates': base_man['isolates']['count'],
       'windows': 'congresses 117-119, all 8 types, BILLSTATUS 2026-08-01 vintage; denominator %d roster pairs' % DEN}
json.dump(out, open(os.path.join(WT, 'audits', 'holistic_v1_eval', 'g5_measurement.json'), 'w', encoding='utf-8'), indent=1)
print('CU (diagnostic ref): pairs %d (%.2f%%) | median w %.3f | median p %.4f | tail>.5 %.4f' %
      (cu['pairs'], cu['density_pct_of_%d' % DEN], cu['weight']['median'], cu['p']['median'], cu['p']['tail_gt_0.5']))
print('HOLISTIC:            pairs %d (%.2f%%) | median w %.3f | median p %.4f | tail>.5 %.4f | tail>.9 %.4f' %
      (hol['pairs'], hol['density_pct_of_%d' % DEN], hol['weight']['median'], hol['p']['median'],
       hol['p']['tail_gt_0.5'], hol['p']['tail_gt_0.9']))
print('D8: tail ratio %s | median dp %.4f | FIRED=%s' % (d8['tail_ratio_gt0.5_hol_over_cu'], d_med, d8['fired']))
print('MEA-3: median regular share %.3f | >0.5 frac %.4f | trigger=%s' %
      (mea3['regular_mass_share']['median'], mea3['regular_mass_share']['gt_0.5_frac'], mea3['trigger_median_gt_0.5']))
print('MEA-4 admitted: %s | MEA-5 isolates: %d' % (man['admitted'], base_man['isolates']['count']))
print('wrote g5_measurement.json')
