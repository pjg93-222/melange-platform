# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""SAY-DO GAP — CU-topical follow-through diagnostic (user's boss, 2026-07-07: "Hill will say a
lot of things, but doesn't follow through on any of them. We may need a method of calculating
follow through").

Construct: the gap between a member's CU RHETORIC and their CU LEGISLATIVE DELIVERY, both
standardized within chamber. This is deliberately DISTINCT from the Volden-Wiseman general
follow-through already in behavioral_dispositions (Hill sits at the 84th percentile there —
he shepherds his bills in general): the boss's observation is CU-SPECIFIC, and the data bears
it out (Hill's sponsored CU bills: 10, advanced past Introduced: 2; Small LENDER Act
reintroduced 117th/118th/119th, never moved).

  SAY(m)  = z_chamber( log1p( recency-weighted supportive CU press volume ) )
  DO(m)   = z_chamber( log1p( sum over CU-tracked bills of role_w x advance_w ) )
            role_w:    sponsor 1.0 | original cosponsor 0.5 | cosponsor 0.25
            advance_w: Introduced 0 | Passed House/Senate/Agreed 1.0
                       | Resolving Differences / Failed-over-veto 1.5 | Became Law 2.0
  GAP(m)  = SAY - DO      (positive = talker; negative = quiet doer)

Also reported per member: sponsored-CU-bill advancement rate (floor: >=3 sponsored to score),
and the LEP general follow-through + percentile as the disambiguating covariate — the
interesting quadrant is HIGH general effectiveness + LOW CU delivery + HIGH CU rhetoric
(capable, loud, not delivering for us specifically).

NON-CHAIN DIAGNOSTIC (kb_benchmark/signed_coalitions pattern): writes say_do_gap.json; no
engine consumes it. Wiring into the desk/tiers is a user decision. Reads member_desk_bundle
(the canonical CU-scoped bills+articles join, regenerated every chain run) + lep_criterion.
Inputs only; safe to run any time."""
import json, math, collections

B = MB_ROOT
d = json.load(open(B + r'\member_desk_bundle.json', encoding='utf-8'))
LEP = json.load(open(B + r'\data/members/lep_criterion.json', encoding='utf-8'))['per_member']

ROLE_W = {'sponsor': 1.0, 'original': 0.5, 'cosponsor': 0.25}
ADV_W = {'Introduced': 0.0, 'Passed House': 1.0, 'Passed Senate': 1.0, 'Agreed to in Senate': 1.0,
         'Resolving Differences': 1.5, 'Failed to pass over veto': 1.5, 'Became Law': 2.0}

raw = {}
for r in d['members']:
    m = r['name']
    arts = r.get('articles') or []
    # supportive-or-neutral CU coverage counts as rhetoric; opposed coverage does not
    say_n = sum(1 for a in arts if (a.get('pol') or 0) >= 0)
    bills = r.get('bills') or []
    do_s = sum(ROLE_W.get(b.get('role'), 0.25) * ADV_W.get(b.get('action'), 0.0) for b in bills)
    sp = [b for b in bills if b.get('role') == 'sponsor']
    adv = [b for b in sp if ADV_W.get(b.get('action'), 0.0) > 0]
    raw[m] = {'chamber': r.get('chamber'), 'say_n': say_n, 'do_score': round(do_s, 3),
              'sponsored_cu': len(sp), 'advanced_cu': len(adv),
              'adv_rate': round(len(adv) / len(sp), 3) if len(sp) >= 3 else None}

def zs(vals):
    mu = sum(vals) / len(vals)
    sd = (sum((v - mu) ** 2 for v in vals) / max(1, len(vals) - 1)) ** .5 or 1.0
    return mu, sd

out = {}
for ch in ('house', 'senate'):
    pool = [m for m in raw if (raw[m]['chamber'] or '').lower() == ch]
    if not pool: continue
    lsay = {m: math.log1p(raw[m]['say_n']) for m in pool}
    ldo = {m: math.log1p(raw[m]['do_score']) for m in pool}
    smu, ssd = zs(list(lsay.values())); dmu, dsd = zs(list(ldo.values()))
    for m in pool:
        say_z = (lsay[m] - smu) / ssd; do_z = (ldo[m] - dmu) / dsd
        gap = say_z - do_z
        quad = ('talker' if gap >= 1.0 else 'doer' if gap <= -1.0 else 'aligned')
        L = LEP.get(m, {})
        out[m] = {**raw[m], 'say_z': round(say_z, 3), 'do_z': round(do_z, 3), 'gap': round(gap, 3),
                  'quadrant': quad, 'lep_followthrough': L.get('followthrough'),
                  'lesclassic': L.get('lesclassic')}

from moneyball_core import dump_atomic
dump_atomic({'members': out,
             'method': 'SAY=z(log1p(supportive CU press n)); DO=z(log1p(sum role_w x advance_w over CU bills)); '
                       'gap=SAY-DO, z within chamber; adv_rate floor 3 sponsored; LEP general follow-through carried '
                       'as covariate. Diagnostic only (2026-07-07, boss follow-through note).'},
            B + r'\say_do_gap.json')

n_t = sum(1 for v in out.values() if v['quadrant'] == 'talker')
n_d = sum(1 for v in out.values() if v['quadrant'] == 'doer')
print('SAY-DO GAP: %d members | talkers (gap>=+1) %d | quiet doers (gap<=-1) %d' % (len(out), n_t, n_d))
top = sorted(out, key=lambda m: -out[m]['gap'])[:10]
bot = sorted(out, key=lambda m: out[m]['gap'])[:10]
print('\nTop talkers (high CU rhetoric, low CU delivery):')
for m in top:
    v = out[m]
    print('  gap %+0.2f  say_z %+0.2f do_z %+0.2f | CU arts %3d | sponsored %2d adv %d | %s' % (
        v['gap'], v['say_z'], v['do_z'], v['say_n'], v['sponsored_cu'], v['advanced_cu'], m))
print('\nQuiet doers (delivery outruns rhetoric):')
for m in bot:
    v = out[m]
    print('  gap %+0.2f  say_z %+0.2f do_z %+0.2f | CU arts %3d | sponsored %2d adv %d | %s' % (
        v['gap'], v['say_z'], v['do_z'], v['say_n'], v['sponsored_cu'], v['advanced_cu'], m))
for m in ('French Hill', 'Mike Crapo', 'Tim Scott', 'Nydia Velazquez', 'Nydia Velázquez', 'Andy Barr'):
    if m in out:
        v = out[m]
        print('\n%s: gap %+0.2f (%s) | say_z %+0.2f do_z %+0.2f | CU arts %d | sponsored %d advanced %d '
              '(rate %s) | LEP general FT %s' % (m, v['gap'], v['quadrant'], v['say_z'], v['do_z'],
              v['say_n'], v['sponsored_cu'], v['advanced_cu'], v['adv_rate'], v['lep_followthrough']))
