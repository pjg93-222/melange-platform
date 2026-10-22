# -*- coding: utf-8 -*-
"""DW-NOMINATE control variable (user directive 2026-07-10: control member antagonism / stability /
agreeableness for ideology). Canonical loader — regenerable from votes/HSall_members.csv (voteview),
NOT a committed JSON blob (regenerate-don't-commit discipline). Returns per roster member:
  dim1      = first-dimension DW-NOMINATE (signed liberal[-]..conservative[+]); ~subsumes party
  dim2      = second dimension
  extremity = |dim1| (distance from center) -- the U-shaped confound: BOTH extremes are more
              combative / cross the aisle less, which a LINEAR control on dim1 cannot capture, so
              extremity must enter residualization sets EXPLICITLY.

Measured confounds (2026-07-10, n=532/533; only James Gallagher lacks a NOMINATE score -- too few
roll calls): cooperation_v2_z (the A criterion) vs |dim1| = -0.250 (25% extremity-confounded --
control is ESSENTIAL for A); reactivity_z (behavioral N/ES) vs |dim1| = -0.004 (ideology-CLEAN --
control is a no-op safety check for ES/N). Hypothesis for the pending hearing-antagonism measure:
high extremity confound (extremists interrupt/defy more) -> |dim1| is a required control there.

Usage: from dw_control import load_dw ; DW = load_dw()  # {member: {'dim1','dim2','extremity'}}
Conditioning-set helper: dw_cols(DW, members) -> np.ndarray [dim1, extremity] for residualization
(pair with party for direction; dim1+extremity gives direction+curvature without the dim1/|dim1|
collinearity trap when party is omitted)."""
import os, csv, json, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

import roster as _roster   # load_dw() has a `roster` parameter of its own
from paths import ROOT
B = ROOT

def load_dw(roster=None):
    """member -> {dim1, dim2, extremity}. roster: optional iterable of member names to restrict to;
    default = all congestion.json members. Latest-congress NOMINATE per bioguide."""
    best = {}
    with open(B + r'\votes\HSall_members.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            b = r.get('bioguide_id')
            if not b:
                continue
            try:
                cong = int(r['congress']); d1 = float(r['nominate_dim1']); d2 = float(r['nominate_dim2'])
            except (ValueError, TypeError):
                continue
            if b not in best or cong > best[b][0]:
                best[b] = (cong, d1, d2)
    C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
    names = roster if roster is not None else C.keys()
    out = {}
    for m in names:
        info = C.get(m) or {}
        t = best.get(info.get('bioguide'))
        if t and t[1] is not None:
            out[m] = {'dim1': t[1], 'dim2': t[2], 'extremity': abs(t[1])}
    return out

def dw_cols(DW, members):
    """[dim1, extremity] design columns aligned to `members` (np.nan where DW is missing)."""
    d1 = np.array([DW[m]['dim1'] if m in DW else np.nan for m in members], float)
    ex = np.array([DW[m]['extremity'] if m in DW else np.nan for m in members], float)
    return np.column_stack([d1, ex])

if __name__ == '__main__':
    DW = load_dw()
    exts = sorted((v['extremity'], m) for m, v in DW.items())
    print('DW-NOMINATE control: %d/%d members' % (len(DW), _roster.count()))
    print('most extreme:', ', '.join('%s %.2f' % (m, e) for e, m in exts[-5:][::-1]))
    print('most centrist:', ', '.join('%s %.2f' % (m, e) for e, m in exts[:5]))
